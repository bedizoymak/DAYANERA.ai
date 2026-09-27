"""Auth/session endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import client_addr, current_user, db_dep, settings_dep
from app.api.schemas import LoginIn, UserOut
from app.core.config import Settings
from app.core.security import SESSION_COOKIE
from app.services import auth as auth_service
from app.services.auth import AuthenticatedUser

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(u: AuthenticatedUser) -> UserOut:
    return UserOut(id=str(u.id), username=u.username, display_name=u.display_name, role=u.role)


@router.post("/login", response_model=UserOut, summary="Yerel oturum aç")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(db_dep),
          settings: Settings = Depends(settings_dep)) -> UserOut:
    res = auth_service.login(db, settings, body.username, body.password,
                             user_agent=request.headers.get("user-agent"), client_addr=client_addr(request))
    if res is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Kullanıcı adı veya parola hatalı.")
    user, token = res
    db.commit()
    response.set_cookie(
        # Secure over HTTPS (LAN reverse proxy); plain http://127.0.0.1 keeps working locally.
        SESSION_COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/",
        max_age=settings.session_ttl_hours * 3600,
    )
    return _user_out(user)


@router.post("/logout", status_code=204, summary="Oturumu kapat")
def logout(response: Response, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> Response:
    auth_service.logout(db, user)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut, summary="Geçerli kullanıcı")
def me(user: AuthenticatedUser = Depends(current_user)) -> UserOut:
    return _user_out(user)
