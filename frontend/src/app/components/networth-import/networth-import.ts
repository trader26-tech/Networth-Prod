import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import {
  NetworthService, UploadResult, SheetMeta, SheetGrid, Meta, Person,
} from '../../services/networth.service';

@Component({
  selector: 'app-networth-import',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './networth-import.html',
  styleUrl: './networth-import.scss',
})
export class NetworthImport {
  private api = inject(NetworthService);
  private router = inject(Router);

  step = signal<1 | 2 | 3>(1);
  uploading = signal(false);
  importing = signal(false);
  error = signal<string | null>(null);

  upload = signal<UploadResult | null>(null);
  sheetSearch = signal('');
  onlyLikely = signal(true);

  grid = signal<SheetGrid | null>(null);
  selectedSheet = signal<string>('');

  headerRow = signal(0);
  dataStart = signal(1);
  dataEnd = signal<number | null>(null);

  colMap = signal<Record<number, string>>({});
  assetClass = signal('other');
  ownerName = signal('Sanjeev');
  risk = signal<string>('');
  valueScale = signal(1);

  colWidth = signal(130);
  rowHeight = signal(30);

  meta = signal<Meta | null>(null);
  people = signal<Person[]>([]);

  result = signal<{ imported: number } | null>(null);
  inr = NetworthService.inr;

  constructor() {
    this.api.meta().subscribe(m => { this.meta.set(m); });
    this.api.people().subscribe(p => this.people.set(p));
  }

  // ---- step 1: upload ----
  onFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) this.doUpload(file);
  }
  onDrop(ev: DragEvent) {
    ev.preventDefault();
    const file = ev.dataTransfer?.files?.[0];
    if (file) this.doUpload(file);
  }
  doUpload(file: File) {
    if (!/\.(xlsx|xlsm)$/i.test(file.name)) { this.error.set('Please choose an .xlsx file.'); return; }
    this.uploading.set(true); this.error.set(null);
    this.api.upload(file).subscribe({
      next: u => { this.upload.set(u); this.uploading.set(false); this.step.set(2); },
      error: e => { this.error.set(e?.error?.detail || 'Upload failed.'); this.uploading.set(false); },
    });
  }

  // ---- step 2: pick sheet ----
  filteredSheets = computed<SheetMeta[]>(() => {
    const u = this.upload(); if (!u) return [];
    const q = this.sheetSearch().toLowerCase();
    return u.sheets.filter(s =>
      (!this.onlyLikely() || s.likely_asset) &&
      s.non_empty_rows > 0 &&
      (!q || s.name.toLowerCase().includes(q)));
  });

  pickSheet(s: SheetMeta) {
    this.selectedSheet.set(s.name);
    this.error.set(null);
    this.api.sheet(this.upload()!.upload_id, s.name, 2000, 120, 0).subscribe({
      next: g => {
        this.grid.set(g);
        const hr = s.header_guess ?? 0;
        this.headerRow.set(hr);
        this.dataStart.set(hr + 1);
        this.dataEnd.set(g.rows.length);
        this.autoMap(g, hr);
        this.guessClass(s.name);
        this.step.set(3);
      },
      error: e => this.error.set(e?.error?.detail || 'Could not load sheet.'),
    });
  }

  // ---- step 3: map ----
  headerCells = computed<any[]>(() => {
    const g = this.grid(); if (!g) return [];
    return g.rows[this.headerRow()] ?? [];
  });
  colCount = computed(() => {
    const g = this.grid(); if (!g) return 0;
    return g.rows.reduce((m, r) => Math.max(m, r.length), 0);
  });
  colIndexes = computed(() => Array.from({ length: this.colCount() }, (_, i) => i));

  autoMap(g: SheetGrid, headerRow: number) {
    const header = (g.rows[headerRow] ?? []).map(c => (c == null ? '' : String(c)).toLowerCase());
    const map: Record<number, string> = {};
    const rules: [string, string[]][] = [
      ['name', ['description', 'particular', 'investment', 'policy', 'location', 'stock', 'item', 'name', 'type of account']],
      ['current_value', ['present value', 'current value', 'amount at present', 'total amount', 'maturity amount', 'present', 'value', 'amount']],
      ['invested_value', ['invested', 'purchased amount', 'principal', 'total invesment', 'initial amount', 'cost', 'premium amount']],
      ['monthly_income', ['monthly income', 'interest per month', 'rent', 'interest/month']],
      ['cagr', ['cagr']],
      ['purchase_date', ['purchased date', 'date of inception', 'start date', 'issue date', 'date']],
      ['owner', ['in favour of', 'in favour  of', 'owner', 'paid by']],
    ];
    const used = new Set<string>();
    header.forEach((h, i) => {
      if (!h.trim()) return;
      for (const [field, kws] of rules) {
        if (used.has(field)) continue;
        if (kws.some(k => h.includes(k))) { map[i] = field; used.add(field); break; }
      }
    });
    this.colMap.set(map);
  }

  guessClass(sheetName: string) {
    const n = sheetName.toLowerCase();
    const m: [string, string][] = [
      ['land', 'real_estate'], ['flat', 'real_estate'], ['property', 'real_estate'],
      ['gold', 'gold'], ['ulip', 'ulip'], ['lic', 'lic'], ['fd', 'fixed_deposit'],
      ['deposit', 'fixed_deposit'], ['po', 'post_office'], ['post', 'post_office'],
      ['share', 'equity'], ['stock', 'equity'], ['loan', 'loan'], ['liabilit', 'loan'],
    ];
    for (const [kw, cls] of m) if (n.includes(kw)) { this.assetClass.set(cls); return; }
    this.assetClass.set('other');
  }

  setCol(idx: number, field: string) {
    this.colMap.update(m => ({ ...m, [idx]: field }));
  }
  colField(idx: number): string { return this.colMap()[idx] || 'ignore'; }

  rowClass(i: number): string {
    if (i === this.headerRow()) return 'hrow';
    if (i < this.dataStart()) return 'pre';
    if (this.dataEnd() != null && i >= this.dataEnd()!) return 'post';
    return 'data';
  }

  mappedCount = computed(() => Object.values(this.colMap()).filter(f => f && f !== 'ignore').length);

  fmt(v: any): string {
    if (v == null || v === '') return '';
    if (typeof v === 'number') return v.toLocaleString('en-IN');
    return String(v);
  }

  // ---- submit ----
  doImport() {
    const g = this.grid(); const u = this.upload();
    if (!g || !u) return;
    const columns = Object.entries(this.colMap())
      .filter(([, f]) => f && f !== 'ignore')
      .map(([i, f]) => ({ index: Number(i), field: f }));
    if (!columns.length) { this.error.set('Map at least one column.'); return; }

    this.importing.set(true); this.error.set(null);
    this.api.import({
      upload_id: u.upload_id,
      sheet: this.selectedSheet(),
      columns,
      header_row: this.headerRow(),
      data_start_row: this.dataStart(),
      data_end_row: this.dataEnd(),
      col_offset: 0,
      max_cols: 60,
      value_scale: Number(this.valueScale()) || 1,
      defaults: {
        asset_class: this.assetClass(),
        risk: this.risk() || null,
        owners: [{ person: this.ownerName(), pct: 100 }],
      },
    }).subscribe({
      next: r => { this.result.set({ imported: r.imported }); this.importing.set(false); },
      error: e => { this.error.set(e?.error?.detail || 'Import failed.'); this.importing.set(false); },
    });
  }

  importAnother() {
    this.result.set(null);
    this.grid.set(null);
    this.colMap.set({});
    this.step.set(2);
  }
  goHome() { this.router.navigate(['/networth']); }

  classLabel(key: string): string {
    return this.meta()?.asset_classes.find(c => c.key === key)?.label ?? key;
  }
}
