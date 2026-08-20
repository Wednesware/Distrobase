CREATE TABLE distributions (
    author TEXT NOT NULL,
    name TEXT NOT NULL,
    latest TEXT,
    PRIMARY KEY (author, name)
);

CREATE TABLE releases (
    author TEXT NOT NULL,
    distribution TEXT NOT NULL,
    version TEXT NOT NULL,
    PRIMARY KEY (author, distribution, version),
    FOREIGN KEY (author, distribution)
        REFERENCES distributions(author, name)
);

CREATE TABLE artifacts (
    author TEXT NOT NULL,
    distribution TEXT NOT NULL,
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    r2_key TEXT NOT NULL,
    PRIMARY KEY (author, distribution, version, name),
    FOREIGN KEY (author, distribution, version)
        REFERENCES releases(author, distribution, version)
);