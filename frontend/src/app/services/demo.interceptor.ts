import { HttpInterceptorFn, HttpResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { of } from 'rxjs';
import { DemoService } from './demo.service';
import { demoResponse } from './demo-data';

/**
 * While demo mode is on, NO request is allowed to reach the network. Every call
 * to `/api/...` is answered from bundled dummy data. This is the hard guarantee
 * that the real user's data can never surface in the investor demo.
 */
export const demoInterceptor: HttpInterceptorFn = (req, next) => {
  const demo = inject(DemoService);
  if (!demo.demo()) return next(req);

  const idx = req.url.indexOf('/api');
  if (idx === -1) return next(req);                 // non-API (assets) → allow

  const apiPath = req.url.slice(idx + 4).split('#')[0];   // strip host + '/api'
  const body = demoResponse(req.method, apiPath);
  return of(new HttpResponse({ status: 200, url: req.url, body }));
};
