from fastapi import Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from src.utils.jwt_utils import decode_access_token
import logging
from src.utils.db import PGDB

db = PGDB()
# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")

from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
auth_scheme = HTTPBearer()


def get_current_user(token: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    # Token decode step
    try:
        logging.info(f"token: {token}")
        payload = decode_access_token(token.credentials)
        if not payload or "sub" not in payload:
            logging.warning("JWT decode failed or missing 'sub' claim.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except Exception as e:
        logging.error(f"JWT decode error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload["sub"])
    # DB lookup step
    try:
        user = db.get_user_by_id(user_id)
        if not user:
            logging.warning(f"User not found in DB for user_id: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found.",
            )
        return user
    except Exception as e:
        logging.error(f"Database error while fetching user_id {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while fetching user"
        )

def error_response(message: str, status_code: int = 400):
    return JSONResponse(
        status_code=status_code,
        content={"error": message}
    )



def is_admin(current_user=Depends(get_current_user)):
    """
    Check if the current user is an admin.
    If not, return a 403 Forbidden response.
    """
    # # Assuming current_user[5] is the admin flag (True/False)
    print(current_user)
    try:
        if current_user[5] == False:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to perform this action."
            )
    except Exception as e:
        logging.error(f"Error checking admin status for user : {e}")
        raise HTTPException(
            status_code=500,
            detail=f"{e}"
        )

    return current_user
