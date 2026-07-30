import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  AionionService, AionionPreview, AionionCommitReport, AionionHolding,
} from '../../services/aionion.service';

@Component({
  selector: 'app-aionion-import',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './aionion-import.html',
  styleUrl: './aionion-import.scss',
})
export class AionionImport {
  private api = inject(AionionService);

  step = signal<1 | 2 | 3>(1);
  loading = signal(false);
  committing = signal(false);
  error = signal<string | null>(null);

  file = signal<File | null>(null);
  owner = signal('');
  preview = signal<AionionPreview | null>(null);
  report = signal<AionionCommitReport | null>(null);
  importHoldings = signal(true);
  importBonds = signal(true);

  inr = AionionService.inr;

  canSubmit = computed(() => !!this.file() && !this.loading());
  equityRows = computed(() =>
    (this.preview()?.holdings ?? []).filter(h => h._kind === 'equity' || h._kind === 'etf'));
  mfRows = computed(() =>
    (this.preview()?.holdings ?? []).filter(h => h._kind === 'mf'));

  onFile(ev: Event) {
    this.file.set((ev.target as HTMLInputElement).files?.[0] ?? null);
    this.error.set(null);
  }

  submit() {
    const f = this.file();
    if (!f) return;
    this.loading.set(true); this.error.set(null);
    this.api.preview(f, this.owner().trim() || undefined).subscribe({
      next: p => { this.preview.set(p); this.step.set(2); this.loading.set(false); },
      error: e => { this.error.set(e?.error?.detail || 'Could not read that file.'); this.loading.set(false); },
    });
  }

  commit() {
    const p = this.preview();
    if (!p) return;
    const sections: string[] = [];
    if (this.importHoldings()) sections.push('holdings');
    if (this.importBonds()) sections.push('bonds');
    if (!sections.length) { this.error.set('Pick at least one section.'); return; }
    this.committing.set(true); this.error.set(null);
    this.api.commit(p, sections).subscribe({
      next: r => { this.report.set(r); this.step.set(3); this.committing.set(false); },
      error: e => { this.error.set(e?.error?.detail || 'Import failed.'); this.committing.set(false); },
    });
  }

  restart() {
    this.step.set(1); this.preview.set(null); this.report.set(null);
    this.file.set(null); this.error.set(null);
  }
}
