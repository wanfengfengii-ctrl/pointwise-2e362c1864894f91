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
CREATE TABLE IF NOT EXISTS protection_roster (
    dispatch_id TEXT NOT NULL PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS protection_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    ready INTEGER NOT NULL CHECK (ready IN (0, 1))
);
CREATE TABLE IF NOT EXISTS protected_requests (
    dispatch_id TEXT NOT NULL,
    request_id TEXT NOT NULL PRIMARY KEY,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    FOREIGN KEY (dispatch_id) REFERENCES protection_roster(dispatch_id)
);
