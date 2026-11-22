CREATE TABLE IF NOT EXISTS review_records (
    review_id INTEGER PRIMARY KEY,
    dispatch_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'withdrawn', 'claimed')),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS review_dispatch_order ON review_records(dispatch_id, review_id);
