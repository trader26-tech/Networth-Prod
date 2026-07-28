import { Injectable } from '@angular/core';
import { Dashboard, Position } from './dashboard.service';

/** What the user chose to include in the export. */
export interface ExportChoice {
  /** asset-class keys to include (e.g. 'apartments', 'land'). */
  classes: Set<string>;
  /** optional sections. */
  summary: boolean;
  holdings: boolean;
  byClass: boolean;
  byPerson: boolean;
}

const money = (v: number | null | undefined): number =>
  v == null || isNaN(v as number) ? 0 : Math.round(v as number);
const pct1 = (v: number | null | undefined): string =>
  v == null || isNaN(v as number) ? '—' : (v * 100).toFixed(1) + '%';
const inr = (v: number | null | undefined): string => {
  if (v == null || isNaN(v as number)) return '—';
  return '₹' + Math.round(v as number).toLocaleString('en-IN');
};

/**
 * Builds Excel (.xlsx) and PDF (.pdf) exports of the wealth dashboard, limited
 * to the asset classes / sections the user picked. Both libraries are lazily
 * imported so they stay out of the initial bundle.
 */
@Injectable({ providedIn: 'root' })
export class ExportService {
  private stamp(): string {
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  /** Positions kept after the class filter, richest first. */
  private pickedPositions(d: Dashboard, c: ExportChoice): Position[] {
    return d.positions
      .filter(p => c.classes.has(p.asset_class))
      .slice()
      .sort((a, b) => b.value - a.value);
  }

  /** Class rows kept after the filter. */
  private pickedClasses(d: Dashboard, c: ExportChoice) {
    return d.by_class.filter(x => c.classes.has(x.asset_class)).slice().sort((a, b) => b.value - a.value);
  }

  /** Person totals recomputed from the picked positions (so they match the filter). */
  private pickedPeople(d: Dashboard, c: ExportChoice) {
    const m = new Map<string, { person: string; value: number; monthly_income: number; count: number }>();
    for (const p of this.pickedPositions(d, c)) {
      const r = m.get(p.owner) || { person: p.owner, value: 0, monthly_income: 0, count: 0 };
      r.value += p.value; r.monthly_income += p.monthly_income; r.count += 1;
      m.set(p.owner, r);
    }
    return [...m.values()].sort((a, b) => b.value - a.value);
  }

  /** Totals over the picked positions. */
  private totals(d: Dashboard, c: ExportChoice) {
    const ps = this.pickedPositions(d, c);
    const value = ps.reduce((s, p) => s + p.value, 0);
    const realisable = ps.reduce((s, p) => s + p.realisable, 0);
    const invested = ps.reduce((s, p) => s + (p.invested || 0), 0);
    const income = ps.reduce((s, p) => s + p.monthly_income, 0);
    return { count: ps.length, value, realisable, invested, income };
  }

  // ── Excel ────────────────────────────────────────────────────────────────
  async excel(d: Dashboard, c: ExportChoice): Promise<void> {
    const XLSX = await import('xlsx');
    const wb = XLSX.utils.book_new();
    const t = this.totals(d, c);

    if (c.summary) {
      const rows: any[][] = [
        ['Wealth Dashboard — Export'],
        ['Generated', this.stamp()],
        [],
        ['Metric', 'Value'],
        ['Net worth (selected)', money(t.value)],
        ['Realisable value', money(t.realisable)],
        ['Invested', money(t.invested)],
        ['Total gain', money(t.value - t.invested)],
        ['Monthly income', money(t.income)],
        ['Annual income', money(t.income * 12)],
        ['Portfolio CAGR', pct1(d.portfolio_cagr)],
        ['Assets included', t.count],
        [],
        ['Full portfolio net worth', money(d.net_worth)],
        ['Full portfolio monthly income', money(d.monthly_income)],
      ];
      const ws = XLSX.utils.aoa_to_sheet(rows);
      ws['!cols'] = [{ wch: 30 }, { wch: 20 }];
      XLSX.utils.book_append_sheet(wb, ws, 'Summary');
    }

    if (c.byClass) {
      const head = ['Asset class', 'Assets', 'Value', 'Allocation %', 'Monthly income', 'CAGR'];
      const body = this.pickedClasses(d, c).map(x => [
        x.label, x.count, money(x.value), +(x.pct * 100).toFixed(1), money(x.monthly_income), pct1(x.cagr),
      ]);
      body.push(['Total', t.count, money(t.value), 100, money(t.income), pct1(d.portfolio_cagr)]);
      const ws = XLSX.utils.aoa_to_sheet([head, ...body]);
      ws['!cols'] = [{ wch: 18 }, { wch: 8 }, { wch: 16 }, { wch: 12 }, { wch: 16 }, { wch: 10 }];
      XLSX.utils.book_append_sheet(wb, ws, 'By class');
    }

    if (c.holdings) {
      const head = ['Asset class', 'Name', 'Owner', 'Detail', 'Value', 'Realisable', 'Invested', 'Gain', 'CAGR', 'Monthly income', 'Liquidity'];
      const body = this.pickedPositions(d, c).map(p => [
        p.class_label, p.name, p.owner, p.sub || '',
        money(p.value), money(p.realisable), p.invested == null ? '' : money(p.invested),
        p.invested == null ? '' : money(p.value - p.invested), pct1(p.cagr),
        money(p.monthly_income), p.liquidity_label,
      ]);
      const ws = XLSX.utils.aoa_to_sheet([head, ...body]);
      ws['!cols'] = [{ wch: 14 }, { wch: 26 }, { wch: 14 }, { wch: 22 }, { wch: 15 }, { wch: 15 },
        { wch: 15 }, { wch: 15 }, { wch: 9 }, { wch: 15 }, { wch: 16 }];
      XLSX.utils.book_append_sheet(wb, ws, 'Holdings');
    }

    if (c.byPerson) {
      const head = ['Person', 'Assets', 'Value', 'Monthly income'];
      const body = this.pickedPeople(d, c).map(p => [p.person, p.count, money(p.value), money(p.monthly_income)]);
      body.push(['Total', t.count, money(t.value), money(t.income)]);
      const ws = XLSX.utils.aoa_to_sheet([head, ...body]);
      ws['!cols'] = [{ wch: 20 }, { wch: 8 }, { wch: 16 }, { wch: 16 }];
      XLSX.utils.book_append_sheet(wb, ws, 'By person');
    }

    if (!wb.SheetNames.length) {
      const ws = XLSX.utils.aoa_to_sheet([['Nothing selected to export.']]);
      XLSX.utils.book_append_sheet(wb, ws, 'Empty');
    }
    XLSX.writeFile(wb, `Networth_${this.stamp()}.xlsx`);
  }

  // ── PDF ──────────────────────────────────────────────────────────────────
  async pdf(d: Dashboard, c: ExportChoice): Promise<void> {
    const { default: jsPDF } = await import('jspdf');
    const autoTable = (await import('jspdf-autotable')).default;

    const doc = new jsPDF({ unit: 'pt', format: 'a4' });
    const W = doc.internal.pageSize.getWidth();
    const M = 40;
    const accent: [number, number, number] = [56, 126, 209];
    const ink: [number, number, number] = [26, 28, 46];
    const muted: [number, number, number] = [122, 128, 150];
    const t = this.totals(d, c);
    let y = M;

    // ── header band ──
    doc.setFillColor(...accent);
    doc.rect(0, 0, W, 8, 'F');
    doc.setTextColor(...ink);
    doc.setFont('helvetica', 'bold'); doc.setFontSize(20);
    doc.text('Wealth Dashboard', M, y + 18);
    doc.setFont('helvetica', 'normal'); doc.setFontSize(10); doc.setTextColor(...muted);
    doc.text(`Generated ${this.stamp()}`, M, y + 34);
    doc.text(`${t.count} assets included`, W - M, y + 34, { align: 'right' });
    y += 58;

    if (c.summary) {
      // KPI cards
      const kpis: [string, string][] = [
        ['Net worth', inr(t.value)],
        ['Monthly income', inr(t.income)],
        ['Invested', inr(t.invested)],
        ['Total gain', inr(t.value - t.invested)],
      ];
      const gap = 12; const cw = (W - M * 2 - gap * (kpis.length - 1)) / kpis.length;
      kpis.forEach((k, i) => {
        const x = M + i * (cw + gap);
        doc.setFillColor(244, 247, 252); doc.setDrawColor(226, 231, 240);
        doc.roundedRect(x, y, cw, 50, 6, 6, 'FD');
        doc.setFont('helvetica', 'normal'); doc.setFontSize(8); doc.setTextColor(...muted);
        doc.text(k[0].toUpperCase(), x + 10, y + 17);
        doc.setFont('helvetica', 'bold'); doc.setFontSize(13); doc.setTextColor(...ink);
        doc.text(k[1], x + 10, y + 37);
      });
      y += 68;
    }

    const table = (title: string, head: string[], body: any[][], foot?: any[]) => {
      doc.setFont('helvetica', 'bold'); doc.setFontSize(12); doc.setTextColor(...ink);
      doc.text(title, M, y);
      y += 8;
      autoTable(doc, {
        startY: y,
        head: [head],
        body,
        foot: foot ? [foot] : undefined,
        margin: { left: M, right: M },
        styles: { fontSize: 8.5, cellPadding: 5, textColor: ink, lineColor: [232, 236, 244], lineWidth: 0.5 },
        headStyles: { fillColor: accent, textColor: [255, 255, 255], fontStyle: 'bold', fontSize: 8.5 },
        footStyles: { fillColor: [244, 247, 252], textColor: ink, fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [250, 251, 253] },
      });
      y = (doc as any).lastAutoTable.finalY + 26;
    };

    if (c.byClass) {
      table('Assets by class',
        ['Asset class', 'Assets', 'Value', 'Alloc %', 'Monthly income', 'CAGR'],
        this.pickedClasses(d, c).map(x => [x.label, x.count, inr(x.value), (x.pct * 100).toFixed(1) + '%', inr(x.monthly_income), pct1(x.cagr)]),
        ['Total', String(t.count), inr(t.value), '100%', inr(t.income), pct1(d.portfolio_cagr)]);
    }

    if (c.byPerson) {
      table('By person',
        ['Person', 'Assets', 'Value', 'Monthly income'],
        this.pickedPeople(d, c).map(p => [p.person, p.count, inr(p.value), inr(p.monthly_income)]),
        ['Total', String(t.count), inr(t.value), inr(t.income)]);
    }

    if (c.holdings) {
      table('Holdings',
        ['Class', 'Name', 'Owner', 'Value', 'Invested', 'CAGR', 'Income/mo'],
        this.pickedPositions(d, c).map(p => [
          p.class_label, p.name, p.owner, inr(p.value),
          p.invested == null ? '—' : inr(p.invested), pct1(p.cagr), inr(p.monthly_income),
        ]));
    }

    // footer page numbers
    const pages = doc.getNumberOfPages();
    for (let i = 1; i <= pages; i++) {
      doc.setPage(i);
      doc.setFont('helvetica', 'normal'); doc.setFontSize(8); doc.setTextColor(...muted);
      doc.text(`Page ${i} of ${pages}`, W - M, doc.internal.pageSize.getHeight() - 18, { align: 'right' });
    }

    doc.save(`Networth_${this.stamp()}.pdf`);
  }
}
