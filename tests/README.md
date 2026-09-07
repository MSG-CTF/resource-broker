# Test suite

The integration tests require an isolated PostgreSQL database because reservation
serialization and one-time enrollment rely on PostgreSQL row locks and partial
unique indexes. SQLite is not a supported substitute.

Install the test dependencies:

```powershell
python -m pip install --editable ".[test]"
```

Start an isolated database whose name begins with `msg_broker_test`:

```powershell
docker run --rm --name msg-broker-test-postgres `
  -e POSTGRES_DB=msg_broker_test `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -p 127.0.0.1:55432:5432 `
  postgres:17-alpine
```

Run the suite in another terminal:

```powershell
$env:TEST_DATABASE_URL = `
  "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/msg_broker_test"
python -m pytest -q
```

The fixture refuses non-PostgreSQL URLs and database names that do not start with
`msg_broker_test` before creating or dropping tables.
