from dataclasses import dataclass
from sqlite3 import Connection

from daily.body import ReportBody, body_fingerprint, encode_report
from daily.freeze import read_frozen
from delivery.ledger import Posting, read_posting
from delivery.receipts import Receipt, read_receipt
from delivery.register import read_admission
from dispatch.catalog import DispatchBinding, read_binding
from dispatch.handoff import read_handoff_state
from intake.revisions import BatchRevision, read_revision


@dataclass(frozen=True)
class AuditEvidence:
    binding: DispatchBinding
    frozen: ReportBody
    parents: tuple[BatchRevision | None, ...]
    status: str
    posting: Posting | None
    receipt: Receipt | None
    differences: tuple[str, ...]


def load_evidence(source: Connection, receiver: Connection | None, dispatch_id: str) -> AuditEvidence:
    differences = []
    parents = []
    source.execute('BEGIN')
    with source:
        binding = read_binding(source, dispatch_id)
        state = read_handoff_state(source, dispatch_id)
        frozen = read_frozen(source, binding.report_ref)
        if body_fingerprint(encode_report(frozen)) != binding.fingerprint:
            differences.append('source:frozen_binding_mismatch')
        for index, ref in enumerate(frozen.parents):
            try:
                parents.append(read_revision(source, ref))
            except KeyError:
                parents.append(None)
                differences.append(f'lineage:missing_parent:{index}')

    entry = posting = receipt = None
    if receiver is not None:
        receiver.execute('BEGIN')
        with receiver:
            entry = read_admission(receiver, dispatch_id)
            try:
                posting = read_posting(receiver, dispatch_id)
            except ValueError:
                differences.append('ledger:invalid_material')
            receipt = read_receipt(receiver, dispatch_id)
    if entry is not None:
        if entry.binding != binding:
            differences.append('receiver:binding_mismatch')
            entry = None
        elif body_fingerprint(entry.material) != binding.fingerprint:
            differences.append('receiver:material_mismatch')

    status = 'pending'
    if state.terminal == 'withdrawn' or (entry is not None and entry.status == 'withdrawn'):
        status = 'withdrawn'
        if state.terminal == 'claimed' or (entry is not None and entry.status == 'claimed'):
            differences.append('disposition:conflicting_terminal_results')
    elif entry is not None and entry.status == 'claimed':
        posting_matches = (posting is not None and posting.dispatch_id == dispatch_id
                           and posting.fingerprint == binding.fingerprint
                           and posting.body.ref == binding.report_ref)
        receipt_matches = receipt is not None and receipt.matches(binding)
        if posting_matches and receipt_matches:
            status = 'claimed'
            if body_fingerprint(encode_report(posting.body)) != binding.fingerprint:
                differences.append('ledger:material_mismatch')
        else:
            if not posting_matches:
                differences.append('ledger:missing_or_unbound_posting')
            if not receipt_matches:
                differences.append('receipt:missing_or_unbound_first_receipt')
    elif state.terminal == 'claimed':
        differences.append('disposition:claim_evidence_unavailable')

    if status != 'claimed':
        if posting is not None or receipt is not None:
            differences.append('disposition:formal_records_without_confirmed_claim')
        posting = receipt = None
    return AuditEvidence(binding, frozen, tuple(parents), status, posting, receipt, tuple(differences))
