import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';

/**
 * Attaches the access token + credentials to every `/api` call, and drops the
 * app to the lock screen on any 401 (token expired server-side → idle lock).
 * Runs after the demo interceptor, so demo mode never reaches here.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (req.url.indexOf('/api') === -1) return next(req);

  const auth = inject(AuthService);
  const token = auth.token();

  const authReq = req.clone({
    withCredentials: true,                          // send the httpOnly device cookie
    setHeaders: token ? { Authorization: `Bearer ${token}` } : {},
  });

  return next(authReq).pipe(
    catchError(err => {
      if (err?.status === 401) auth.onUnauthorized();
      return throwError(() => err);
    }),
  );
};
