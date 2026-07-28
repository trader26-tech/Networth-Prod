import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse, HttpClient } from '@angular/common/http';
import { DomSanitizer, SafeResourceUrl, SafeHtml } from '@angular/platform-browser';
import { Observable } from 'rxjs';
import { DocumentsService, VaultFolder } from '../../services/documents.service';
import { LandService } from '../../services/land.service';
import { ApartmentsService } from '../../services/apartments.service';
import { GoldService } from '../../services/gold.service';

type Domain = 'land' | 'apartments' | 'gold';
interface DocFile { id: string; filename: string; mime_type: string | null; size: number | null; created_at: string; }
interface Entity { id: string; name: string; documents: DocFile[]; }

const DOMAIN_NAME: Record<Domain, string> = { land: 'Land', apartments: 'Apartments', gold: 'Gold & Silver' };

// one flat, ordered list of visible rows (folders/files), expand-in-place
type Row =
  | { t: 'folder'; key: string; id: string; name: string; depth: number; files: number }
  | { t: 'vfile'; key: string; id: string; name: string; size: number | null; date: string; depth: number; folderId: string; mime: string | null }
  | { t: 'lgroup'; key: string; domain: Domain; name: string; depth: number; files: number }
  | { t: 'lentity'; key: string; domain: Domain; id: string; name: string; depth: number; files: number }
  | { t: 'lfile'; key: string; domain: Domain; id: string; name: string; size: number | null; date: string; depth: number; mime: string | null };

@Component({
  selector: 'app-documents',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './documents.html',
  styleUrl: './documents.scss',
})
export class Documents implements OnInit {
  private api = inject(DocumentsService);
  private landSvc = inject(LandService);
  private aptSvc = inject(ApartmentsService);
  private goldSvc = inject(GoldService);
  private sanitizer = inject(DomSanitizer);
  private http = inject(HttpClient);

  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  folders = signal<VaultFolder[]>([]);
  private filesCache = signal<Record<string, DocFile[]>>({});
  entities = signal<Record<Domain, Entity[]>>({ land: [], apartments: [], gold: [] });

  expanded = signal<Set<string>>(new Set());
  uploadingKey = signal<string | null>(null);

  // Finder-style detail/preview pane (right side)
  selected = signal<{ id: string; name: string; url: string; mime: string | null; size: number | null; date: string; where: string } | null>(null);
  safeUrl = computed<SafeResourceUrl | null>(() => {
    const s = this.selected();
    return s ? this.sanitizer.bypassSecurityTrustResourceUrl(s.url) : null;
  });
  // mini-viewer content (rendered inside the square preview)
  previewHtml = signal<SafeHtml | null>(null);    // parsed spreadsheet table
  previewText = signal<string | null>(null);      // text / json / csv-as-text
  previewLoading = signal(false);

  selectFile(id: string, name: string, mime: string | null, size: number | null, date: string, url: string, where: string) {
    this.selected.set({ id, name, url, mime, size, date, where });
    this.previewHtml.set(null); this.previewText.set(null); this.previewLoading.set(false);
    const k = this.pkind(mime, name);
    if (k === 'sheet') this.loadSheet(url);
    else if (k === 'text') this.loadText(url);
    // on phones the preview sits above the list — scroll it into view
    if (typeof window !== 'undefined' && window.innerWidth <= 920) {
      setTimeout(() => document.querySelector('.detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
    }
  }
  isPdf(mime: string | null, name: string): boolean {
    return (!!mime && mime.toLowerCase().includes('pdf')) || this.ext(name).toLowerCase() === 'pdf';
  }
  /** which mini-viewer to render for a file. */
  pkind(mime: string | null, name: string): 'image' | 'pdf' | 'sheet' | 'text' | 'other' {
    if (this.isImage(mime)) return 'image';
    if (this.isPdf(mime, name)) return 'pdf';
    const e = this.ext(name).toLowerCase(); const m = (mime || '').toLowerCase();
    if (['xlsx', 'xls', 'csv'].includes(e) || m.includes('sheet') || m.includes('excel') || m.includes('csv')) return 'sheet';
    if (['txt', 'json', 'md', 'log', 'xml', 'yml', 'yaml'].includes(e) || m.startsWith('text/')) return 'text';
    return 'other';
  }
  private loadSheet(url: string) {
    this.previewLoading.set(true);
    this.http.get(url, { responseType: 'arraybuffer' }).subscribe({
      next: async buf => {
        try {
          const XLSX = await import('xlsx');          // lazy — keeps it out of the initial bundle
          const wb = XLSX.read(buf, { type: 'array' });
          const ws = wb.Sheets[wb.SheetNames[0]];
          this.previewHtml.set(this.sanitizer.bypassSecurityTrustHtml(XLSX.utils.sheet_to_html(ws)));
        } catch { this.previewHtml.set(null); }
        this.previewLoading.set(false);
      },
      error: () => this.previewLoading.set(false),
    });
  }
  private loadText(url: string) {
    this.previewLoading.set(true);
    this.http.get(url, { responseType: 'text' }).subscribe({
      next: t => { this.previewText.set(t.slice(0, 20000)); this.previewLoading.set(false); },
      error: () => this.previewLoading.set(false),
    });
  }

  // inline editing state
  renamingId = signal<string | null>(null);   // 'fld:<id>' for folders, '<id>' for files
  renameText = signal('');
  addingSubFor = signal<string | null>(null);  // folder id we're adding a subfolder under ('' = top level)
  newName = signal('');

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true); this.error.set(null); this.needsMigration.set(false);
    this.api.folders().subscribe({
      next: f => { this.folders.set(f); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not reach the API. Is the backend running?');
      },
    });
    this.landSvc.parcels().subscribe({ next: (p: any[]) => this.setEntities('land', p), error: () => {} });
    this.aptSvc.units().subscribe({ next: (u: any[]) => this.setEntities('apartments', u), error: () => {} });
    this.goldSvc.items().subscribe({ next: (g: any[]) => this.setEntities('gold', g), error: () => {} });
  }
  private setEntities(d: Domain, rows: any[]) {
    this.entities.update(e => ({ ...e, [d]: rows.map(x => ({ id: x.id, name: x.name, documents: x.documents || [] })) }));
  }

  // ── expand / collapse ───────────────────────────────────────────────────────
  private toggle(key: string) {
    this.expanded.update(s => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; });
  }
  toggleFolder(id: string) { if (!this.expanded().has(id)) this.ensureFiles(id); this.toggle(id); }
  toggleGroup(d: Domain) { this.toggle('dom:' + d); }
  toggleEntity(d: Domain, id: string) { this.toggle('ent:' + d + ':' + id); }
  isExp(key: string) { return this.expanded().has(key); }

  private ensureFiles(folderId: string) {
    if (this.filesCache()[folderId]) return;
    this.api.files(folderId).subscribe({ next: f => this.filesCache.update(c => ({ ...c, [folderId]: f })), error: () => {} });
  }

  // ── the flat row list ───────────────────────────────────────────────────────
  rows = computed<Row[]>(() => {
    const out: Row[] = [];
    const all = this.folders();
    const childrenOf = (pid: string | null) => all.filter(f => (f.parent_id || null) === pid).sort((a, b) => a.name.localeCompare(b.name));
    const walk = (pid: string | null, depth: number) => {
      for (const f of childrenOf(pid)) {
        out.push({ t: 'folder', key: 'fld:' + f.id, id: f.id, name: f.name, depth, files: f.file_count });
        if (this.expanded().has(f.id)) {
          for (const file of (this.filesCache()[f.id] || []))
            out.push({ t: 'vfile', key: 'vf:' + file.id, id: file.id, name: file.filename, size: file.size, date: this.fmtDate(file.created_at), depth: depth + 1, folderId: f.id, mime: file.mime_type });
          walk(f.id, depth + 1);
        }
      }
    };
    walk(null, 0);
    // linked — only domains/entities that actually have documents (no empty auto-folders)
    for (const d of ['land', 'apartments', 'gold'] as Domain[]) {
      const withDocs = this.entities()[d].filter(e => e.documents.length > 0);
      if (!withDocs.length) continue;
      const total = withDocs.reduce((a, e) => a + e.documents.length, 0);
      out.push({ t: 'lgroup', key: 'g:' + d, domain: d, name: DOMAIN_NAME[d], depth: 0, files: total });
      if (this.expanded().has('dom:' + d)) {
        for (const e of withDocs) {
          out.push({ t: 'lentity', key: 'e:' + d + ':' + e.id, domain: d, id: e.id, name: e.name, depth: 1, files: e.documents.length });
          if (this.expanded().has('ent:' + d + ':' + e.id))
            for (const f of e.documents)
              out.push({ t: 'lfile', key: 'lf:' + f.id, domain: d, id: f.id, name: f.filename, size: f.size, date: this.fmtDate(f.created_at), depth: 2, mime: f.mime_type });
        }
      }
    }
    return out;
  });

  hasAnything = computed(() => this.rows().length > 0);

  // ── new folder / subfolder ──────────────────────────────────────────────────
  startAddFolder(parentId: string) { this.addingSubFor.set(parentId); this.newName.set(''); }   // '' = top level
  createFolder() {
    const name = this.newName().trim();
    const parent = this.addingSubFor();
    if (!name) { this.addingSubFor.set(null); return; }
    this.api.createFolder(name, parent || null).subscribe({
      next: () => { this.addingSubFor.set(null); if (parent) this.expanded.update(s => new Set(s).add(parent)); this.reloadFolders(); },
      error: () => this.error.set('Could not create the folder.'),
    });
  }
  private reloadFolders() { this.api.folders().subscribe({ next: f => this.folders.set(f), error: () => {} }); }

  // ── folder rename / delete ──────────────────────────────────────────────────
  startRename(key: string, name: string) { this.renamingId.set(key); this.renameText.set(name); }
  cancelRename() { this.renamingId.set(null); this.renameText.set(''); }
  saveFolderName(id: string) {
    const name = this.renameText().trim();
    if (!name) { this.cancelRename(); return; }
    this.api.renameFolder(id, name).subscribe({ next: () => { this.cancelRename(); this.reloadFolders(); }, error: () => this.error.set('Could not rename.') });
  }
  deleteFolder(id: string, name: string, ev: Event) {
    ev.stopPropagation();
    if (!confirm(`Delete folder "${name}" and everything inside it?`)) return;
    this.api.deleteFolder(id).subscribe({ next: () => this.reloadFolders(), error: () => this.error.set('Could not delete the folder.') });
  }

  // ── uploads ─────────────────────────────────────────────────────────────────
  onPickVault(ev: Event, folderId: string) { this.pick(ev, () => folderId, 'fld:' + folderId, 'vault'); }
  onPickEntity(ev: Event, d: Domain, entityId: string) { this.pick(ev, () => entityId, 'e:' + d + ':' + entityId, d); }
  private pick(ev: Event, target: () => string, key: string, where: 'vault' | Domain) {
    const input = ev.target as HTMLInputElement;
    const files = input.files ? Array.from(input.files) : [];
    input.value = '';
    if (!files.length) return;
    this.uploadingKey.set(key);
    let done = 0;
    const finish = () => { if (++done >= files.length) { this.uploadingKey.set(null); this.afterChange(where, target()); } };
    for (const f of files) {
      const req: Observable<any> = where === 'vault' ? this.api.uploadFile(target(), f) : this.domainSvc(where).uploadDocument(target(), f);
      req.subscribe({ next: () => finish(), error: () => { this.error.set('Upload failed — ' + f.name); finish(); } });
    }
  }
  private afterChange(where: 'vault' | Domain, id: string) {
    if (where === 'vault') {
      this.filesCache.update(c => { const n = { ...c }; delete n[id]; return n; });
      this.expanded.update(s => new Set(s).add(id));
      this.ensureFiles(id); this.reloadFolders();
    } else { this.reloadDomain(where); }
  }

  // ── file rename / delete / open ─────────────────────────────────────────────
  saveVFile(row: Row & { t: 'vfile' }, folderId: string) {
    const name = this.renameText().trim();
    if (!name || name === row.name) { this.cancelRename(); return; }
    this.api.renameFile(row.id, name).subscribe({ next: () => { this.cancelRename(); this.filesCache.update(c => { const n = { ...c }; delete n[folderId]; return n; }); this.ensureFiles(folderId); }, error: () => this.error.set('Could not rename.') });
  }
  saveLFile(row: Row & { t: 'lfile' }) {
    const name = this.renameText().trim();
    if (!name || name === row.name) { this.cancelRename(); return; }
    this.domainSvc(row.domain).renameDocument(row.id, name).subscribe({ next: () => { this.cancelRename(); this.reloadDomain(row.domain); }, error: () => this.error.set('Could not rename.') });
  }
  deleteVFile(id: string, name: string, folderId: string) {
    if (!confirm(`Delete "${name}"?`)) return;
    this.api.deleteFile(id).subscribe({ next: () => { if (this.selected()?.id === id) this.selected.set(null); this.filesCache.update(c => { const n = { ...c }; delete n[folderId]; return n; }); this.ensureFiles(folderId); this.reloadFolders(); }, error: () => this.error.set('Could not delete.') });
  }
  deleteLFile(d: Domain, id: string, name: string) {
    if (!confirm(`Delete "${name}"?`)) return;
    this.domainSvc(d).deleteDocument(id).subscribe({ next: () => { if (this.selected()?.id === id) this.selected.set(null); this.reloadDomain(d); }, error: () => this.error.set('Could not delete.') });
  }
  vaultUrl(id: string) { return this.api.fileUrl(id); }
  linkedUrl(d: Domain, id: string) { return this.domainSvc(d).documentUrl(id); }

  // folder id that a vfile row belongs to (its parent folder is the nearest expanded folder above)
  private domainSvc(d: Domain): any { return d === 'land' ? this.landSvc : d === 'apartments' ? this.aptSvc : this.goldSvc; }
  private reloadDomain(d: Domain) {
    const obs: Observable<any[]> = d === 'land' ? this.landSvc.parcels() : d === 'apartments' ? this.aptSvc.units() : this.goldSvc.items();
    obs.subscribe({ next: (rows: any[]) => this.setEntities(d, rows), error: () => {} });
  }

  // ── helpers ─────────────────────────────────────────────────────────────────
  fmtBytes(n: number | null): string {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }
  fmtDate(d: string | null): string {
    if (!d) return '';
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? '' : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  indent(depth: number) { return depth * 22 + 12; }

  // ── file preview helpers ────────────────────────────────────────────────────
  isImage(mime: string | null): boolean { return !!mime && mime.toLowerCase().startsWith('image/'); }
  /** image source that asks the backend to transcode HEIC/HEIF/TIFF → JPEG. */
  prev(url: string): string { return url + (url.includes('?') ? '&' : '?') + 'preview=1'; }
  ext(name: string): string {
    const parts = (name || '').split('.');
    return parts.length > 1 ? parts.pop()!.toUpperCase().slice(0, 4) : 'FILE';
  }
  /** type class for the preview box colour. */
  kind(name: string, mime: string | null): string {
    if (this.isImage(mime)) return 'img';
    const m = (mime || '').toLowerCase(); const e = this.ext(name).toLowerCase();
    if (m.includes('pdf') || e === 'pdf') return 'pdf';
    if (m.includes('sheet') || m.includes('csv') || ['csv', 'xls', 'xlsx'].includes(e)) return 'sheet';
    if (m.includes('word') || ['doc', 'docx'].includes(e)) return 'doc';
    if (m.includes('zip') || ['zip', 'rar', '7z'].includes(e)) return 'zip';
    return 'other';
  }

  migrationSql = `CREATE TABLE IF NOT EXISTS document_folders (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  parent_id   TEXT REFERENCES document_folders(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vault_documents (
  id            TEXT PRIMARY KEY,
  folder_id     TEXT NOT NULL REFERENCES document_folders(id) ON DELETE CASCADE,
  filename      TEXT NOT NULL,
  mime_type     TEXT,
  size          BIGINT,
  storage_path  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);`;
  copied = signal(false);
  copySql() { navigator.clipboard?.writeText(this.migrationSql).then(() => { this.copied.set(true); setTimeout(() => this.copied.set(false), 1500); }); }
}
