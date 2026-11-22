CREATE TABLE IF NOT EXISTS stations (
    station_id TEXT NOT NULL PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT NOT NULL PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(station_id)
);
CREATE TABLE IF NOT EXISTS batch_revisions (
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    revision INTEGER NOT NULL CHECK (revision > 0),
    parent_revision INTEGER CHECK (parent_revision < revision),
    PRIMARY KEY (batch_id, revision),
    FOREIGN KEY (batch_id, parent_revision) REFERENCES batch_revisions(batch_id, revision)
);
CREATE TABLE IF NOT EXISTS observations (
    batch_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK (position > 0),
    liters TEXT,
    missing TEXT,
    source TEXT NOT NULL,
    CHECK ((liters IS NULL) <> (missing IS NULL)),
    PRIMARY KEY (batch_id, revision, position),
    FOREIGN KEY (batch_id, revision) REFERENCES batch_revisions(batch_id, revision)
);
CREATE TABLE IF NOT EXISTS frozen_reports (
    report_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    material TEXT NOT NULL,
    PRIMARY KEY (report_id, revision)
);
CREATE TABLE IF NOT EXISTS dispatches (
    dispatch_id TEXT NOT NULL PRIMARY KEY,
    report_id TEXT NOT NULL,
    report_revision INTEGER NOT NULL,
    destination TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'registered'
        CHECK (phase IN ('registered', 'staging', 'granted', 'admitted')),
    terminal TEXT CHECK (terminal IN ('claimed', 'withdrawn')),
    CHECK (terminal IS NULL OR terminal = 'withdrawn' OR phase = 'admitted'),
    FOREIGN KEY (report_id, report_revision) REFERENCES frozen_reports(report_id, revision)
);
CREATE TABLE IF NOT EXISTS withdraw_requests (
    request_id TEXT NOT NULL PRIMARY KEY,
    dispatch_id TEXT NOT NULL REFERENCES dispatches(dispatch_id),
    result TEXT CHECK (result IN ('claimed', 'withdrawn'))
);
