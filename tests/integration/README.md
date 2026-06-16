# Integration Tests

This directory is reserved for future pytest-based integration tests.

Current live AWS integration coverage is orchestrated by the phase scripts under
`scripts/aws/`, because those flows manage Docker containers, AWS credentials,
runtime config rendering, stack lifecycle, and cleanup.
