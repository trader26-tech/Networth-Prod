import { Injectable, signal } from '@angular/core';

/**
 * Demo mode — lets a prospective investor explore the whole app with realistic
 * **dummy** data, without ever touching the real backend or the owner's data.
 *
 * The flag lives in sessionStorage (so it ends when the tab closes) and is read
 * by the HTTP interceptor (`demo.interceptor.ts`), which short-circuits EVERY
 * `/api` request to bundled dummy responses. Nothing reaches the network, so
 * the real user's data can never be revealed in demo mode.
 */
const KEY = 'nw_demo_mode';

@Injectable({ providedIn: 'root' })
export class DemoService {
  /** Reactive flag — true while exploring the sample dashboard. */
  readonly demo = signal<boolean>(
    typeof sessionStorage !== 'undefined' && sessionStorage.getItem(KEY) === '1',
  );

  enter() {
    if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(KEY, '1');
    this.demo.set(true);
  }

  exit() {
    if (typeof sessionStorage !== 'undefined') sessionStorage.removeItem(KEY);
    this.demo.set(false);
  }
}
