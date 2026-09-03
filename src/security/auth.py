"""
JWT 认证模块

提供基于 JWT 的身份认证，支持：
- Token 生成与验证
- Token 刷新机制
- API Key 认证（备选）

安全设计（修复硬编码凭据）:
- JWT 密钥: 仅从环境变量 JWT_SECRET 读取；开发环境未配置时使用随机临时密钥
  （重启后所有 token 失效）；生产环境（FINSCOPE_ENV=production）未配置则拒绝启动
- 用户口令: 存放于外部 users 文件（PBKDF2-SHA256 哈希），源码零口令；
  首次启动自动生成 admin 随机口令并打印到控制台（仅此一次）
- API Key: 从外部文件 / 环境变量加载，源码零密钥
"""

import os
import time
import json
import secrets
import hashlib
import hmac
import base64
import logging
import threading
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 100_000


def hash_password(password: str, salt_hex: str = None) -> str:
    """PBKDF2-SHA256 口令哈希，返回 pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>"""
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令（常数时间比较）"""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


class TokenManager:
    """Token 管理器"""

    def __init__(self, secret_key: str = None, expire_minutes: int = 60):
        if secret_key:
            self.secret_key = secret_key
        elif os.getenv("JWT_SECRET"):
            self.secret_key = os.getenv("JWT_SECRET")
        elif os.getenv("FINSCOPE_ENV", "").lower() == "production":
            raise RuntimeError(
                "生产环境必须通过环境变量 JWT_SECRET 配置 JWT 密钥，拒绝使用临时密钥启动"
            )
        else:
            # 随机临时密钥: 源码零密钥；代价是重启后所有 token 失效（开发态可接受）
            self.secret_key = secrets.token_hex(32)
            logger.warning(
                "JWT_SECRET 未配置，已生成随机临时密钥（重启后所有 token 失效）。"
                "生产环境请务必配置 JWT_SECRET 环境变量"
            )
        self.expire_minutes = expire_minutes

    def generate_token(self, user_id: str, role: str, extra_claims: Dict = None) -> str:
        """生成 JWT Token"""
        payload = {
            "sub": user_id,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + (self.expire_minutes * 60),
            "jti": hashlib.sha256(f"{user_id}{time.time()}{secrets.token_hex(8)}".encode()).hexdigest()[:16],
        }
        if extra_claims:
            payload.update(extra_claims)

        return self._encode_payload(payload)

    def verify_token(self, token: str) -> Optional[Dict]:
        """验证 Token"""
        try:
            payload = self._decode_payload(token)
            if not payload:
                return None

            # 检查过期时间
            if payload.get("exp", 0) < time.time():
                return None

            return payload
        except Exception:
            return None

    def refresh_token(self, token: str, extend_minutes: int = 30) -> Optional[str]:
        """刷新 Token"""
        payload = self.verify_token(token)
        if not payload:
            return None

        # 延长过期时间
        payload["exp"] = int(time.time()) + (extend_minutes * 60)
        payload["iat"] = int(time.time())

        return self._encode_payload(payload)

    def _encode_payload(self, payload: Dict) -> str:
        """编码 JWT Payload"""
        header = {"alg": "HS256", "typ": "JWT"}

        header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

        signature_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            self.secret_key.encode(),
            signature_input.encode(),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def _decode_payload(self, token: str) -> Optional[Dict]:
        """解码 JWT Payload"""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            header_b64, payload_b64, signature_b64 = parts

            # 验证签名
            signature_input = f"{header_b64}.{payload_b64}"
            expected_signature = hmac.new(
                self.secret_key.encode(),
                signature_input.encode(),
                hashlib.sha256
            ).digest()
            expected_signature_b64 = base64.urlsafe_b64encode(expected_signature).decode().rstrip("=")

            if not hmac.compare_digest(signature_b64, expected_signature_b64):
                return None

            # 解码 payload
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            return json.loads(payload_bytes)

        except Exception:
            return None


class UsersStore:
    """
    用户口令存储（外部文件，源码零口令）

    文件格式 (data/users.json):
    {
        "admin": {"password_hash": "pbkdf2_sha256$100000$...$...", "role": "admin"},
        "analyst": {"password": "明文（加载时自动升级为哈希并回写）", "role": "analyst"}
    }

    环境变量:
    - FINSCOPE_USERS_FILE: 自定义 users 文件路径
    - FINSCOPE_USERS_JSON: 直接注入 JSON（测试/容器场景优先级最高）
    """

    def __init__(self, users_file: str = None):
        self.users_file = users_file or os.getenv("FINSCOPE_USERS_FILE", "data/users.json")
        self._lock = threading.Lock()
        self._users: Dict[str, Dict[str, str]] = self._load_or_bootstrap()

    def _load_or_bootstrap(self) -> Dict[str, Dict[str, str]]:
        env_json = os.getenv("FINSCOPE_USERS_JSON")
        if env_json:
            try:
                return self._normalize(json.loads(env_json))
            except Exception as e:
                logger.error("FINSCOPE_USERS_JSON 解析失败: %s", str(e)[:100])
                return {}

        if os.path.isfile(self.users_file):
            try:
                with open(self.users_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                users = self._normalize(raw)
                # 明文口令自动升级为哈希并回写
                if any("password" in u for u in raw.values()):
                    self._save(users)
                    logger.info("检测到明文口令，已自动升级为 PBKDF2 哈希: %s", self.users_file)
                return users
            except Exception as e:
                logger.error("users 文件加载失败 (%s): %s", self.users_file, str(e)[:100])
                return {}

        # 首次启动: 引导生成 admin + 随机口令
        return self._bootstrap()

    def _bootstrap(self) -> Dict[str, Dict[str, str]]:
        bootstrap_password = secrets.token_urlsafe(9)
        users = {
            "admin": {"password_hash": hash_password(bootstrap_password), "role": "admin"},
        }
        try:
            self._save(users)
            # 初始口令仅在首次生成的控制台输出一次，文件中只存哈希
            logger.warning(
                "首次启动已生成初始管理员（文件: %s）\n"
                "  用户名: admin\n  初始口令: %s\n"
                "  ⚠️ 该口令仅本次显示，请登录后尽快修改并删除本日志",
                self.users_file, bootstrap_password,
            )
        except Exception as e:
            logger.error("users 文件写入失败 (%s): %s", self.users_file, str(e)[:100])
        return users

    @staticmethod
    def _normalize(raw: Dict) -> Dict[str, Dict[str, str]]:
        users: Dict[str, Dict[str, str]] = {}
        for username, info in raw.items():
            if not isinstance(info, dict):
                continue
            entry = {"role": info.get("role", "analyst")}
            if info.get("password_hash"):
                entry["password_hash"] = info["password_hash"]
            elif info.get("password"):
                entry["password_hash"] = hash_password(info["password"])
            else:
                continue
            users[username] = entry
        return users

    def _save(self, users: Dict[str, Dict[str, str]]):
        directory = os.path.dirname(self.users_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.users_file, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    def verify(self, username: str, password: str) -> Optional[str]:
        """校验口令，成功返回角色"""
        with self._lock:
            entry = self._users.get(username)
        if not entry:
            return None
        if not verify_password(password, entry.get("password_hash", "")):
            return None
        return entry.get("role", "analyst")


class JWTAuth:
    """JWT 认证器"""

    def __init__(self, token_manager: TokenManager = None, users_store: UsersStore = None):
        self.token_manager = token_manager or TokenManager()
        self.users_store = users_store or UsersStore()

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """
        用户认证

        口令校验基于外部 users 文件（PBKDF2 哈希），生产环境应对接 LDAP/AD。
        """
        role = self.users_store.verify(username, password)
        if role is None:
            return None

        return self.token_manager.generate_token(username, role)

    def verify(self, token: str) -> Optional[Dict]:
        """验证 Token"""
        return self.token_manager.verify_token(token)

    def get_current_user(self, token: str) -> Optional[Dict]:
        """获取当前用户信息"""
        payload = self.verify(token)
        if not payload:
            return None

        return {
            "user_id": payload.get("sub"),
            "role": payload.get("role"),
            "token_id": payload.get("jti"),
            "issued_at": datetime.fromtimestamp(payload.get("iat", 0)).isoformat(),
            "expires_at": datetime.fromtimestamp(payload.get("exp", 0)).isoformat(),
        }


class APIKeyAuth:
    """
    API Key 认证（备选方案）

    密钥从外部加载，源码零密钥:
    - FINSCOPE_API_KEYS_JSON: 直接注入 JSON（优先）
    - FINSCOPE_API_KEYS_FILE: 文件路径（默认 data/api_keys.json）
      格式: {"<key>": {"user": "...", "role": "..."}}
    """

    def __init__(self, keys_file: str = None):
        self.api_keys: Dict[str, Dict[str, str]] = {}
        self._load(
            keys_file or os.getenv("FINSCOPE_API_KEYS_FILE", "data/api_keys.json")
        )

    def _load(self, path: str):
        env_json = os.getenv("FINSCOPE_API_KEYS_JSON")
        if env_json:
            try:
                self.api_keys = json.loads(env_json)
                return
            except Exception as e:
                logger.error("FINSCOPE_API_KEYS_JSON 解析失败: %s", str(e)[:100])
                return

        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.api_keys = json.load(f)
                logger.info("API Key 文件已加载: %s (%d 个)", path, len(self.api_keys))
            except Exception as e:
                logger.error("API Key 文件加载失败 (%s): %s", path, str(e)[:100])
        else:
            logger.warning(
                "未配置 API Key 文件 (%s)，API Key 认证不可用。"
                "创建该文件（格式 {\"key\": {\"user\":..., \"role\":...}}）后重启生效",
                path,
            )

    def verify_api_key(self, api_key: str) -> Optional[Dict]:
        """验证 API Key（常数时间比较）"""
        for key, info in self.api_keys.items():
            if hmac.compare_digest(key, api_key or ""):
                return info
        return None
