import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AuthService } from './auth.service';

function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}

export interface VaultFolder {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string;
  file_count: number;
  subfolder_count: number;
}

export interface VaultFile {
  id: string;
  folder_id: string;
  filename: string;
  mime_type: string | null;
  size: number | null;
  created_at: string;
}

@Injectable({ providedIn: 'root' })
export class DocumentsService {
  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private base = `${_apiBase()}/documents`;

  // ── folders ───────────────────────────────────────────────────────────────
  folders(): Observable<VaultFolder[]> { return this.http.get<VaultFolder[]>(`${this.base}/folders`); }
  createFolder(name: string, parent_id: string | null): Observable<VaultFolder> {
    return this.http.post<VaultFolder>(`${this.base}/folders`, { name, parent_id });
  }
  renameFolder(id: string, name: string): Observable<VaultFolder> {
    return this.http.patch<VaultFolder>(`${this.base}/folders/${id}`, { name });
  }
  deleteFolder(id: string): Observable<any> { return this.http.delete(`${this.base}/folders/${id}`); }

  // ── files ─────────────────────────────────────────────────────────────────
  files(folderId: string): Observable<VaultFile[]> {
    return this.http.get<VaultFile[]>(`${this.base}/folders/${folderId}/files`);
  }
  uploadFile(folderId: string, file: File): Observable<VaultFile> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<VaultFile>(`${this.base}/folders/${folderId}/files`, fd);
  }
  renameFile(id: string, name: string): Observable<VaultFile> {
    return this.http.patch<VaultFile>(`${this.base}/files/${id}`, { name });
  }
  deleteFile(id: string): Observable<any> { return this.http.delete(`${this.base}/files/${id}`); }
  // The file stream is loaded directly by the browser (<img>, <iframe>, an
  // "open in new tab" link), which can't send the Authorization header the
  // interceptor adds to XHR — so we pass the same access token as a query
  // param, which the API's auth guard accepts as a fallback.
  fileUrl(id: string): string {
    const t = this.auth.token();
    return t ? `${this.base}/files/${id}?access_token=${encodeURIComponent(t)}` : `${this.base}/files/${id}`;
  }
}
