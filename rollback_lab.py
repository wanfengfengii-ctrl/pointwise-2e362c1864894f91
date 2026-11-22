import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

from daily.freeze import freeze
from delivery.claim import claim
from delivery.register import initialize_delivery, read_admission
from dispatch.catalog import dispatch, initialize_source
from dispatch.resume import resume_handoff
from intake.batches import record
from tests.fixtures import DESTINATION, ORIGINAL, Interrupted


PROTECTED = ("D", "E")


def seed_dispatches(source):
    revision = record(source, "north-am", "north", ORIGINAL)
    original = freeze(source, "day", [revision.ref])
    next_issue = freeze(source, "day", [revision.ref])
    for dispatch_id, report in (("D", original), ("E", next_issue), ("V1", next_issue)):
        dispatch(source, dispatch_id, report.ref, DESTINATION)
    return original, next_issue


def open_receiver(path):
    receiver = sqlite3.connect(path)
    try:
        initialize_delivery(receiver, DESTINATION)
    except BaseException:
        receiver.close()
        raise
    return receiver


def save_image(receiver, target):
    with closing(sqlite3.connect(target)) as backup:
        receiver.backup(backup)


def replace_receiver(receiver, live_path, image_path):
    image_uri = Path(image_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(image_uri, uri=True)) as backup:
        receiver.close()
        Path(live_path).unlink()
        save_image(backup, live_path)
    return open_receiver(live_path)


class CutCustodyCall:
    def __init__(self, call, cut):
        if cut not in ("before_confirm", "after_confirm"):
            raise ValueError("unknown custody interruption point")
        self.call = call
        self.cut = cut

    def __call__(self, operation, payload):
        if operation != "confirm" or self.cut is None:
            return self.call(operation, payload)
        cut, self.cut = self.cut, None
        if cut == "after_confirm":
            self.call(operation, payload)
        raise Interrupted(cut)


def compare_first(first, repeated, rolled_back, original):
    if first != repeated or first.report != original or rolled_back.report != original:
        raise AssertionError("V1 report or live replay changed")
    observed = (
        first.receipt.receipt_id, first.receipt.request_id,
        rolled_back.receipt.receipt_id, rolled_back.receipt.request_id,
        rolled_back.report.totals.liters,
    )
    if observed != ("D/first", "R1", "D/first", "R2", 18):
        raise AssertionError(observed)
    return {
        "receipt_id": observed[0], "original_first": observed[1],
        "backup_first": observed[3], "liters": observed[4],
    }


def show_v1():
    with TemporaryDirectory(prefix="daily-rollback-") as folder, ExitStack() as resources:
        root = Path(folder)
        live_path = root / "receiver.sqlite"
        early = root / "before-d.sqlite"
        late = root / "before-claim.sqlite"
        receiver = resources.enter_context(closing(open_receiver(live_path)))
        with closing(sqlite3.connect(root / "source.sqlite")) as source:
            initialize_source(source)
            original, _ = seed_dispatches(source)
            for dispatch_id in ("E", "V1"):
                if resume_handoff(source, receiver, dispatch_id) != "admitted":
                    raise AssertionError(dispatch_id)
            claim(receiver, "V1", "ordinary-request")
            save_image(receiver, early)
            if resume_handoff(source, receiver, "D") != "admitted":
                raise AssertionError("D admission failed")
            save_image(receiver, late)
        first = claim(receiver, "D", "R1")
        repeated = claim(receiver, "D", "R2")
        receiver = resources.enter_context(closing(replace_receiver(receiver, live_path, early)))
        if read_admission(receiver, "D") is not None:
            raise AssertionError("early image already contains D")
        receiver = resources.enter_context(closing(replace_receiver(receiver, live_path, late)))
        observed = compare_first(first, repeated, claim(receiver, "D", "R2"), original)
        print("V1_ROLLBACK " + json.dumps(observed, sort_keys=True))


if __name__ == "__main__":
    show_v1()
