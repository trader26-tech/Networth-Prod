import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import {
  CasService, CasPreview, CasBond, CasHolding, CasCommitReport,
} from '../../services/cas.service';

@Component({
  selector: 'app-cas-import',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './cas-import.html',
  styleUrl: './cas-import.scss',
})
export class CasImport {
  private api = inject(CasService);
  private router = inject(Router);

  step = signal<1 | 2 | 3>(1);
  loading = signal(false);
  committing = signal(false);
  error = signal<string | null>(null);

  file = signal<File | null>(null);
  pan = signal('');
  owner = signal('');

  preview = signal<CasPreview | null>(null);
  report = signal<CasCommitReport | null>(null);

  importHoldings = signal(true);
  importBonds = signal(true);

  inr = CasService.inr;

  /** A PAN is 5 letters, 4 digits, 1 letter — validate before uploading. */
  panValid = computed(() => /^[A-Za-z]{5}\d{4}[A-Za-z]$/.test(this.pan().trim()));
  canSubmit = computed(() => !!this.file() && this.panValid() && !this.loading());

  /** Bonds the CAS could not fully describe — these need your input. */
  bondsNeedingWork = computed(() =>
    (this.preview()?.bonds ?? []).filter(b => !b.maturity_date));

  totalValue = computed(() => {
    const p = this.preview();
    if (!p) return 0;
    const h = p.holdings.reduce((s, x) => s + (x._value ?? 0), 0);
    const b = p.bonds.reduce((s, x) => s + (x._value ?? 0), 0);
    return h + b;
  });

  onFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0] ?? null;
    this.file.set(f);
    this.error.set(null);
  }

  submit() {
    const f = this.file();
    if (!f || !this.panValid()) return;
    this.loading.set(true);
    this.error.set(null);
    this.api.preview(f, this.pan().trim().toUpperCase(), this.owner().trim() || undefined)
      .subscribe({
        next: (p) => {
          this.preview.set(p);
          this.step.set(2);
          this.loading.set(false);
        },
        error: (e) => {
          this.error.set(e?.error?.detail || e?.message || 'Could not read that CAS.');
          this.loading.set(false);
        },
      });
  }

  commit() {
    const p = this.preview();
    if (!p) return;
    const sections: string[] = [];
    if (this.importHoldings()) sections.push('holdings');
    if (this.importBonds()) sections.push('bonds');
    if (!sections.length) {
      this.error.set('Pick at least one section to import.');
      return;
    }
    this.committing.set(true);
    this.error.set(null);
    this.api.commit(p, sections).subscribe({
      next: (r) => {
        this.report.set(r);
        this.step.set(3);
        this.committing.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || e?.message || 'Import failed.');
        this.committing.set(false);
      },
    });
  }

  /** Let the user fix a missing maturity date before committing. */
  setMaturity(bond: CasBond, value: string) {
    bond.maturity_date = value || null;
    this.preview.set({ ...this.preview()! });
  }

  setCoupon(bond: CasBond, value: string) {
    const n = parseFloat(value);
    bond.coupon_rate = isNaN(n) ? 0 : n;
    this.preview.set({ ...this.preview()! });
  }

  restart() {
    this.step.set(1);
    this.preview.set(null);
    this.report.set(null);
    this.file.set(null);
    this.error.set(null);
  }

  topHoldings(): CasHolding[] {
    return [...(this.preview()?.holdings ?? [])]
      .sort((a, b) => (b._value ?? 0) - (a._value ?? 0))
      .slice(0, 25);
  }
}
