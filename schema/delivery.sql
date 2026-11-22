CREATE TABLE IF NOT EXISTS receiver_site (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    destination TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
    dispatch_id TEXT NOT NULL PRIMARY KEY,
    report_id TEXT NOT NULL,
    report_revision INTEGER NOT NULL,
    destination TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    material TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'admitted', 'claimed', 'withdrawn'))
);
CREATE TABLE IF NOT EXISTS claim_requests (
    request_id TEXT NOT NULL PRIMARY KEY,
    dispatch_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS postings (
    dispatch_id TEXT NOT NULL PRIMARY KEY REFERENCES inbox(dispatch_id),
    fingerprint TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    dispatch_id TEXT NOT NULL PRIMARY KEY REFERENCES postings(dispatch_id),
    receipt_id TEXT NOT NULL UNIQUE,
    destination TEXT NOT NULL,
    report_id TEXT NOT NULL,
    report_revision INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE REFERENCES claim_requests(request_id)
);
