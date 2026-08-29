import html2canvas from 'html2canvas';
import { jsPDF } from 'jspdf';

export type ReportExportFormat = 'image' | 'pdf' | 'word';

function triggerBlobDownload(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

async function captureReportCanvas(element: HTMLElement): Promise<HTMLCanvasElement> {
  return html2canvas(element, {
    backgroundColor: '#f8fafc',
    scale: 2,
    useCORS: true,
    windowWidth: element.scrollWidth,
    windowHeight: element.scrollHeight,
  });
}

function sanitizeBaseName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'report';
}

async function exportAsImage(canvas: HTMLCanvasElement, baseName: string) {
  const link = document.createElement('a');
  link.download = `${baseName}.png`;
  link.href = canvas.toDataURL('image/png');
  link.click();
}

async function exportAsPdf(canvas: HTMLCanvasElement, baseName: string) {
  const pdf = new jsPDF('p', 'mm', 'a4');
  const pageWidthMm = 210;
  const pageHeightMm = 297;
  const pageHeightPx = (canvas.width / pageWidthMm) * pageHeightMm;
  let renderedHeightPx = 0;
  let firstPage = true;

  while (renderedHeightPx < canvas.height) {
    const sliceHeightPx = Math.min(pageHeightPx, canvas.height - renderedHeightPx);
    const pageCanvas = document.createElement('canvas');
    pageCanvas.width = canvas.width;
    pageCanvas.height = sliceHeightPx;

    const pageCtx = pageCanvas.getContext('2d');
    if (!pageCtx) {
      throw new Error('Failed to create PDF page context');
    }

    pageCtx.drawImage(
      canvas,
      0,
      renderedHeightPx,
      canvas.width,
      sliceHeightPx,
      0,
      0,
      canvas.width,
      sliceHeightPx,
    );

    const imageData = pageCanvas.toDataURL('image/png');
    const sliceHeightMm = (sliceHeightPx * pageWidthMm) / canvas.width;

    if (!firstPage) {
      pdf.addPage();
    }
    firstPage = false;

    pdf.addImage(imageData, 'PNG', 0, 0, pageWidthMm, sliceHeightMm);
    renderedHeightPx += sliceHeightPx;
  }

  pdf.save(`${baseName}.pdf`);
}

async function exportAsWord(canvas: HTMLCanvasElement, baseName: string) {
  const imageData = canvas.toDataURL('image/png');
  const html = `<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>${baseName}</title>
    <style>
      @page { size: A4 portrait; margin: 12mm; }
      body { margin: 0; font-family: Arial, sans-serif; }
      img { width: 100%; height: auto; display: block; }
    </style>
  </head>
  <body>
    <img src="${imageData}" alt="${baseName}" />
  </body>
</html>`;

  const blob = new Blob(['\ufeff', html], {
    type: 'application/msword;charset=utf-8',
  });
  triggerBlobDownload(blob, `${baseName}.doc`);
}

export async function exportWholeReport(
  element: HTMLElement,
  rawBaseName: string,
  format: ReportExportFormat,
): Promise<void> {
  const baseName = sanitizeBaseName(rawBaseName);
  const canvas = await captureReportCanvas(element);

  if (format === 'image') {
    await exportAsImage(canvas, baseName);
    return;
  }
  if (format === 'pdf') {
    await exportAsPdf(canvas, baseName);
    return;
  }
  await exportAsWord(canvas, baseName);
}
