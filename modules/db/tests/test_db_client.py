import pytest

from modules.db.client import DBClient, ReadOnlyViolation, assert_read_only


# ------------------------------------------------------------------
# assert_read_only
# ------------------------------------------------------------------

class TestAssertReadOnly:
    @pytest.mark.parametrize("sql", [
        "SELECT * FROM users WHERE id = 1",
        "select id, email from users",
        "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
        "EXPLAIN SELECT * FROM users",
        "SHOW statement_timeout",
        "TABLE users",
        "  SELECT 1  ",
        "SELECT 1;",
    ])
    def test_allows_reads(self, sql):
        assert_read_only(sql)  # does not raise

    @pytest.mark.parametrize("sql", [
        "",
        "   ",
    ])
    def test_rejects_empty(self, sql):
        with pytest.raises(ReadOnlyViolation, match="empty query"):
            assert_read_only(sql)

    @pytest.mark.parametrize("sql", [
        "INSERT INTO users (email) VALUES ('x')",
        "UPDATE users SET email = 'x' WHERE id = 1",
        "DELETE FROM users WHERE id = 1",
        "DROP TABLE users",
        "TRUNCATE users",
        "ALTER TABLE users ADD COLUMN x int",
        "CREATE TABLE x (id int)",
        "GRANT SELECT ON users TO someone",
        "VACUUM users",
        "CALL some_procedure()",
    ])
    def test_rejects_write_first_word(self, sql):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(sql)

    def test_rejects_write_hidden_in_cte(self):
        sql = "WITH deleted AS (DELETE FROM users WHERE id = 1 RETURNING *) SELECT * FROM deleted"
        with pytest.raises(ReadOnlyViolation, match="write/DDL keyword"):
            assert_read_only(sql)

    def test_rejects_select_into(self):
        with pytest.raises(ReadOnlyViolation, match="INTO"):
            assert_read_only("SELECT * INTO new_table FROM users")

    def test_rejects_stacked_statements(self):
        with pytest.raises(ReadOnlyViolation, match="stacked"):
            assert_read_only("SELECT 1; DROP TABLE users;")

    def test_single_trailing_semicolon_is_fine(self):
        assert_read_only("SELECT 1;")  # does not raise

    def test_rejects_unknown_statement_type(self):
        with pytest.raises(ReadOnlyViolation, match="only SELECT"):
            assert_read_only("ANALYZE users")


# ------------------------------------------------------------------
# DBClient — config validation (no real connection attempted)
# ------------------------------------------------------------------

class TestDBClientConfig:
    """
    `load_module_env` reads config/db/.env straight off disk (see
    shared/config_loader.py) — monkeypatch.delenv alone can't simulate
    "config absent" if a developer happens to have a real config/db/.env
    checked out locally. Pointing WORKFLOW_HOME at an empty tmp_path makes
    config_dir() resolve to a directory with no such file, regardless of
    what's really on disk for this developer.
    """

    @pytest.fixture(autouse=True)
    def isolated_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKFLOW_HOME", str(tmp_path))

    def test_missing_host_raises(self, monkeypatch):
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.setenv("DB_NAME", "db")
        monkeypatch.setenv("DB_USER", "u")
        with pytest.raises(RuntimeError, match="DB_HOST"):
            DBClient()

    def test_missing_name_raises(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "localhost")
        monkeypatch.delenv("DB_NAME", raising=False)
        monkeypatch.setenv("DB_USER", "u")
        with pytest.raises(RuntimeError, match="DB_HOST"):
            DBClient()

    def test_ssh_host_without_key_raises(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "postgres.internal")
        monkeypatch.setenv("DB_NAME", "db")
        monkeypatch.setenv("DB_USER", "u")
        monkeypatch.setenv("DB_SSH_HOST", "bastion.example.com")
        monkeypatch.delenv("DB_SSH_USER", raising=False)
        monkeypatch.delenv("DB_SSH_KEY", raising=False)
        with pytest.raises(RuntimeError, match="DB_SSH_USER/DB_SSH_KEY"):
            DBClient()
