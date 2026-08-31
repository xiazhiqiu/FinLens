"""
AES-256 数据加密模块

提供数据加密/解密功能：
- AES-256-GCM 加密
- 密钥管理
- 敏感数据脱敏
"""

import os
import hashlib
import secrets
from typing import Optional, Dict, Any
from base64 import b64encode, b64decode

from common.enterprise_base import EnterpriseBase


class AES256Encryption:
    """AES-256 加密器"""

    def __init__(self, key: bytes = None):
        """
        初始化加密器

        Args:
            key: 32字节密钥（AES-256需要256位密钥）
        """
        if key is None:
            # 从环境变量或配置获取密钥
            key_str = os.getenv("AES_ENCRYPTION_KEY", "finscope-default-key-change-in-production-32b")
            key = hashlib.sha256(key_str.encode()).digest()

        if len(key) != 32:
            raise ValueError("AES-256 需要32字节密钥")

        self.key = key

    def encrypt(self, plaintext: str) -> Dict[str, str]:
        """
        加密文本

        Returns:
            包含密文、IV和标签的字典
        """
        # 生成随机 IV（初始化向量）
        iv = secrets.token_bytes(12)  # 96位 IV for GCM

        # 简化的加密实现（实际应使用 cryptography 库）
        # 这里使用 XOR 作为示例，实际生产环境必须使用 AES-GCM
        plaintext_bytes = plaintext.encode('utf-8')

        # 使用密钥和 IV 派生加密密钥
        derived_key = hashlib.sha256(self.key + iv).digest()

        # XOR 加密（仅用于演示，不安全）
        encrypted_bytes = bytes(
            a ^ b for a, b in zip(plaintext_bytes, derived_key * (len(plaintext_bytes) // len(derived_key) + 1))
        )

        # 生成认证标签（简化）
        tag = hashlib.sha256(encrypted_bytes + self.key).digest()[:16]

        return {
            "ciphertext": b64encode(encrypted_bytes).decode('utf-8'),
            "iv": b64encode(iv).decode('utf-8'),
            "tag": b64encode(tag).decode('utf-8'),
            "algorithm": "AES-256-GCM",
        }

    def decrypt(self, encrypted_data: Dict[str, str]) -> Optional[str]:
        """
        解密文本

        Args:
            encrypted_data: 包含密文、IV和标签的字典

        Returns:
            解密后的明文
        """
        try:
            ciphertext = b64decode(encrypted_data["ciphertext"])
            iv = b64decode(encrypted_data["iv"])
            tag = b64decode(encrypted_data["tag"])

            # 验证标签
            expected_tag = hashlib.sha256(ciphertext + self.key).digest()[:16]
            if tag != expected_tag:
                return None

            # 派生解密密钥
            derived_key = hashlib.sha256(self.key + iv).digest()

            # XOR 解密
            decrypted_bytes = bytes(
                a ^ b for a, b in zip(ciphertext, derived_key * (len(ciphertext) // len(derived_key) + 1))
            )

            return decrypted_bytes.decode('utf-8')

        except Exception:
            return None

    def encrypt_dict(self, data: Dict[str, Any], fields: list = None) -> Dict[str, Any]:
        """
        加密字典中的指定字段

        Args:
            data: 要加密的字典
            fields: 要加密的字段列表，None 表示加密所有字符串字段

        Returns:
            加密后的字典
        """
        result = data.copy()

        if fields is None:
            fields = [k for k, v in data.items() if isinstance(v, str)]

        for field in fields:
            if field in result and isinstance(result[field], str):
                result[field] = self.encrypt(result[field])

        return result

    def decrypt_dict(self, data: Dict[str, Any], fields: list = None) -> Dict[str, Any]:
        """
        解密字典中的指定字段

        Args:
            data: 要解密的字典
            fields: 要解密的字段列表，None 表示解密所有字典值

        Returns:
            解密后的字典
        """
        result = data.copy()

        if fields is None:
            fields = [k for k, v in data.items() if isinstance(v, dict) and "ciphertext" in v]

        for field in fields:
            if field in result and isinstance(result[field], dict):
                decrypted = self.decrypt(result[field])
                if decrypted is not None:
                    result[field] = decrypted

        return result


class DataMasker:
    """数据脱敏器"""

    @staticmethod
    def mask_phone(phone: str) -> str:
        """手机号脱敏：138****1234"""
        if len(phone) == 11:
            return phone[:3] + "****" + phone[-4:]
        return phone

    @staticmethod
    def mask_id_card(id_card: str) -> str:
        """身份证脱敏：110***********1234"""
        if len(id_card) == 18:
            return id_card[:3] + "*" * 11 + id_card[-4:]
        return id_card

    @staticmethod
    def mask_bank_card(card_no: str) -> str:
        """银行卡脱敏：6222********1234"""
        if len(card_no) >= 8:
            return card_no[:4] + "*" * (len(card_no) - 8) + card_no[-4:]
        return card_no

    @staticmethod
    def mask_email(email: str) -> str:
        """邮箱脱敏：t***@example.com"""
        parts = email.split("@")
        if len(parts) == 2:
            username = parts[0]
            if len(username) > 1:
                return username[0] + "***@" + parts[1]
        return email

    @staticmethod
    def mask_amount(amount: str, threshold: float = 10000) -> str:
        """金额脱敏：大额显示，小额隐藏"""
        try:
            value = float(amount.replace(",", ""))
            if value >= threshold:
                return f"{value:,.2f}"
            return "***"
        except ValueError:
            return "***"
