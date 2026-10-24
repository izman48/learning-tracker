"""Password hashing and the length rule.

argon2id only (pwdlib's defaults). The 128-character cap is checked before any
hashing, so a huge password cannot be used to burn CPU.
"""

from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

MIN_PASSWORD = 10
MAX_PASSWORD = 128

password_helper = PasswordHelper(PasswordHash((Argon2Hasher(),)))
