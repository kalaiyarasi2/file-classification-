import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { BarChart3, Search, Download } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useSettings } from "@/lib/store";
import { toast } from "sonner";

export const Route = createFileRoute("/reports")({ component: ReportsPage });

function ReportsPage() {
  const defaultOut = useSettings((s) => s.defaultOutputFolder);
  const [folder, setFolder] = useState(defaultOut);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<"file_name" | "category" | "confidence">("file_name");
  const [page, setPage] = useState(1);
  const pageSize = 15;

  const { data, refetch, isFetching, error } = useQuery({
    queryKey: ["report", folder], queryFn: () => api.getReport(folder), enabled: false, retry: false,
  });

  const filtered = useMemo(() => {
    const rows = data?.rows ?? [];
    const f = rows
      .filter((r) => !filter || r.category === filter)
      .filter((r) => !search || r.file_name.toLowerCase().includes(search.toLowerCase()))
      .sort((a, b) => String(a[sortKey] ?? "").localeCompare(String(b[sortKey] ?? "")));
    return f;
  }, [data, search, filter, sortKey]);

  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const categories = Array.from(new Set((data?.rows ?? []).map((r) => r.category)));

  function exportCsv() { window.open(api.downloadReportUrl(folder), "_blank"); }
  function exportJson() {
    const blob = new Blob([JSON.stringify(filtered, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "report.json"; a.click();
  }
  function exportExcel() {
    // simple TSV that Excel imports cleanly
    const headers = ["file_name", "category", "pdf_type", "confidence", "processing_time", "destination_folder", "error"];
    const rows = [headers.join("\t"), ...filtered.map((r) => headers.map((h) => (r as any)[h] ?? "").join("\t"))];
    const blob = new Blob([rows.join("\n")], { type: "application/vnd.ms-excel" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "report.xls"; a.click();
  }

  return (
    <>
      <PageHeader icon={BarChart3} title="Reports" description="Browse, filter and export the classification_report.csv produced by the pipeline." />

      <Panel className="mb-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[260px]">
            <Label className="text-[11.5px]">Output Folder</Label>
            <Input value={folder} onChange={(e) => setFolder(e.target.value)} className="h-8 mt-1 font-mono text-[12px]" />
          </div>
          <Button size="sm" onClick={() => { setPage(1); refetch().then((r) => r.error && toast.error((r.error as any).message)); }} disabled={isFetching}>
            {isFetching ? "Loading…" : "Load Report"}
          </Button>
        </div>
        {error && <div className="text-[12px] text-destructive mt-2">{(error as any).message}</div>}
      </Panel>

      {data && (
        <Panel
          title={`${data.total_rows} rows`}
          description={data.report_path}
          actions={
            <>
              <Button size="sm" variant="outline" onClick={exportCsv}><Download className="w-3 h-3" /> CSV</Button>
              <Button size="sm" variant="outline" onClick={exportExcel}><Download className="w-3 h-3" /> Excel</Button>
              <Button size="sm" variant="outline" onClick={exportJson}><Download className="w-3 h-3" /> JSON</Button>
            </>
          }
        >
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <div className="relative">
              <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search filename" className="h-7 pl-6 text-[12px] w-52" />
            </div>
            <select value={filter} onChange={(e) => setFilter(e.target.value)} className="h-7 px-2 rounded-md border border-input bg-background text-[12px]">
              <option value="">All categories</option>
              {categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={sortKey} onChange={(e) => setSortKey(e.target.value as any)} className="h-7 px-2 rounded-md border border-input bg-background text-[12px]">
              <option value="file_name">Sort: File name</option>
              <option value="category">Sort: Category</option>
              <option value="confidence">Sort: Confidence</option>
            </select>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-muted-foreground border-b border-border">
                  <th className="py-2 px-2 font-medium">File</th>
                  <th className="py-2 px-2 font-medium">Category</th>
                  <th className="py-2 px-2 font-medium">Type</th>
                  <th className="py-2 px-2 font-medium text-right">Confidence</th>
                  <th className="py-2 px-2 font-medium text-right">Time</th>
                  <th className="py-2 px-2 font-medium">Destination</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((r, i) => (
                  <tr key={i} className="border-b border-border last:border-0">
                    <td className="py-1.5 px-2 font-mono truncate max-w-[180px]">{r.file_name}</td>
                    <td className="py-1.5 px-2 text-primary font-medium">{r.category}</td>
                    <td className="py-1.5 px-2 capitalize">{r.pdf_type}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{r.confidence}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{r.processing_time}</td>
                    <td className="py-1.5 px-2 font-mono text-muted-foreground truncate max-w-[180px]">{r.destination_folder}</td>
                  </tr>
                ))}
                {paged.length === 0 && (
                  <tr><td colSpan={6} className="py-6 text-center text-muted-foreground">No matching rows.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between mt-3 text-[11.5px]">
            <span className="text-muted-foreground">Page {page} of {totalPages}</span>
            <div className="flex gap-1">
              <Button size="sm" variant="outline" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>Prev</Button>
              <Button size="sm" variant="outline" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages}>Next</Button>
            </div>
          </div>
        </Panel>
      )}
    </>
  );
}
