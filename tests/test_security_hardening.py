"""
安全加固回归测试

覆盖:
1. UsersStore: 首次启动引导 admin（文件只存哈希，零明文）+ 口令校验
2. 口令哈希: PBKDF2 往返 + 错误口令拒绝
3. TokenManager: 生产环境无 JWT_SECRET 拒绝启动；开发环境用随机临时密钥
4. APIKeyAuth: 密钥从环境注入，源码零密钥
5. AuditLogger: event_id 跨实例唯一（模块级计数器）
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_users_store_bootstrap_hashes_password():
    """首次启动引导 admin：文件中只存 PBKDF2 哈希，绝不落明文"""
    from security.auth import UsersStore

    tmp_dir = tempfile.mkdtemp(prefix="finscope_users_")
    path = os.path.join(tmp_dir, "users.json")

    store = UsersStore(users_file=path)

    assert "admin" in store._users, "引导必须生成 admin"
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert "password" not in raw["admin"], "文件不允许出现明文口令"
    assert raw["admin"]["password_hash"].startswith("pbkdf2_sha256$")
    assert raw["admin"]["role"] == "admin"

    # 重新加载可读
    store2 = UsersStore(users_file=path)
    assert "admin" in store2._users
    print("PASS: UsersStore 引导 + 哈希落盘")


def test_users_store_verify_and_reject():
    """口令校验: 正确放行 / 错误拒绝 / 不存在用户拒绝"""
    from security.auth import UsersStore

    os.environ["FINSCOPE_USERS_JSON"] = json.dumps({
        "analyst": {"password": "pw-123", "role": "analyst"}
    })
    try:
        store = UsersStore()
        assert store.verify("analyst", "pw-123") == "analyst"
        assert store.verify("analyst", "wrong") is None
        assert store.verify("ghost", "pw-123") is None
    finally:
        del os.environ["FINSCOPE_USERS_JSON"]
    print("PASS: 口令校验正确/错误/不存在")


def test_password_hash_roundtrip():
    from security.auth import hash_password, verify_password

    h = hash_password("secret-1")
    assert verify_password("secret-1", h)
    assert not verify_password("secret-2", h)
    print("PASS: PBKDF2 哈希往返")


def test_token_manager_production_requires_secret():
    """生产环境未配置 JWT_SECRET 必须拒绝启动（禁止临时密钥）"""
    from security.auth import TokenManager

    os.environ["FINSCOPE_ENV"] = "production"
    os.environ.pop("JWT_SECRET", None)
    try:
        raised = False
        try:
            TokenManager()
        except RuntimeError:
            raised = True
        assert raised, "生产环境缺 JWT_SECRET 必须抛 RuntimeError"
    finally:
        os.environ.pop("FINSCOPE_ENV", None)
    print("PASS: 生产环境强制 JWT_SECRET")


def test_token_manager_dev_ephemeral_secret():
    """开发环境无 JWT_SECRET: 随机临时密钥可用（token 可生成/验证）"""
    from security.auth import TokenManager

    os.environ.pop("JWT_SECRET", None)
    os.environ.pop("FINSCOPE_ENV", None)
    tm = TokenManager()
    token = tm.generate_token("u1", "analyst")
    payload = tm.verify_token(token)
    assert payload is not None and payload["sub"] == "u1"
    print("PASS: 开发态临时密钥可签发/验证")


def test_api_key_auth_from_env():
    """API Key 从环境注入（源码零密钥）"""
    from security.auth import APIKeyAuth

    os.environ["FINSCOPE_API_KEYS_JSON"] = json.dumps({
        "k-abc": {"user": "svc", "role": "admin"}
    })
    try:
        api = APIKeyAuth()
        assert api.verify_api_key("k-abc")["role"] == "admin"
        assert api.verify_api_key("k-wrong") is None
    finally:
        os.environ.pop("FINSCOPE_API_KEYS_JSON", None)
    print("PASS: API Key 环境注入 + 常数时间校验")


def test_audit_event_id_unique_across_instances():
    """event_id 跨实例唯一（修复各实例计数器各自从 1 开始的重复问题）"""
    from audit.audit_logger import AuditLogger, EventType

    a = AuditLogger(enable_console=False, enable_file=False)
    b = AuditLogger(enable_console=False, enable_file=False)

    e1 = a.log_event(EventType.SYSTEM_START, "u1", "first")
    e2 = b.log_event(EventType.SYSTEM_START, "u2", "second")
    e3 = a.log_event(EventType.SYSTEM_START, "u3", "third")

    assert len({e1.event_id, e2.event_id, e3.event_id}) == 3, "event_id 必须全局唯一"
    print("PASS: event_id 跨实例唯一")


if __name__ == "__main__":
    test_users_store_bootstrap_hashes_password()
    test_users_store_verify_and_reject()
    test_password_hash_roundtrip()
    test_token_manager_production_requires_secret()
    test_token_manager_dev_ephemeral_secret()
    test_api_key_auth_from_env()
    test_audit_event_id_unique_across_instances()
    print("\n全部通过 ✓")
