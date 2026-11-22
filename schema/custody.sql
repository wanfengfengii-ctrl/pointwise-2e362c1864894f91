CREATE TABLE IF NOT EXISTS custody_site (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    destination TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custody_confirmations (
    dispatch_id TEXT NOT NULL PRIMARY KEY,
    report_id TEXT NOT NULL,
    report_revision INTEGER NOT NULL,
    destination TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    material TEXT NOT NULL,
    first_request_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custody_requests (
    dispatch_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    PRIMARY KEY (dispatch_id, request_id),
    FOREIGN KEY (dispatch_id) REFERENCES custody_confirmations(dispatch_id)
);
