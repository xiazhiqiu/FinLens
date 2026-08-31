"""
JWT 认证模块

提供基于 JWT 的身份认证，支持：
- Token 生成与验证
- Token 刷新机制
- API Key 认证（备选）
"""

import os
import time
import hashlib
import hmac
import json
import base64
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from common.enterprise_base import EnterpriseBase


class TokenManager:
    """Token 管理器"""

    def __init__(self, secret_key: str = None, expire_minutes: int = 60):
        self.secret_key = secret_key or os.getenv("JWT_SECRET", "finscope-enterprise-secret-key-change-in-production")
        self.expire_minutes = expire_minutes

    def generate_token(self, user_id: str, role: str, extra_claims: Dict = None) -> str:
        """生成 JWT Token"""
        payload = {
            "sub": user_id,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + (self.expire_minutes * 60),
            "jti": hashlib.sha256(f"{user_id}{time.time()}".encode()).hexdigest()[:16],
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


class JWTAuth:
    """JWT 认证器"""

    def __init__(self, token_manager: TokenManager = None):
        self.token_manager = token_manager or TokenManager()

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """
        用户认证（示例实现，实际应连接 LDAP/AD）
        """
        # TODO: 实际应连接企业 LDAP/AD
        # 这里是示例用户数据库
        users_db = {
            "analyst": {"password": "analyst123", "role": "analyst"},
            "reviewer": {"password": "reviewer123", "role": "reviewer"},
            "compliance": {"password": "compliance123", "role": "compliance"},
            "admin": {"password": "admin123", "role": "admin"},
        }

        user = users_db.get(username)
        if not user or user["password"] != password:
            return None

        return self.token_manager.generate_token(username, user["role"])

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
    """API Key 认证（备选方案）"""

    def __init__(self):
        # 示例 API Keys，实际应从数据库或配置中心获取
        self.api_keys = {
            "finscope-demo-key-001": {"user": "system", "role": "admin"},
            "finscope-demo-key-002": {"user": "analyst01", "role": "analyst"},
        }

    def verify_api_key(self, api_key: str) -> Optional[Dict]:
        """验证 API Key"""
        return self.api_keys.get(api_key)
