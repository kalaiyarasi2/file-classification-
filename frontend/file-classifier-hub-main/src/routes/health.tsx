import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Activity, RefreshCw, CheckCircle2, XCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { Panel, StatCard } from "@/components/Panel";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { api, getBackendUrl } from "@/lib/api";

export const Route = createFileRoute("/health")({ component: HealthPage });

function HealthPage() {
  const [auto, setAuto] = useState(true);
  const [latency, setLatency] = useState<number | null>(null);

  const { data, refetch, isFetching, error } = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const t0 = performance.now();
      const r = await api.health();
      setLatency(Math.round(performance.now() - t0));
      return r;
    },
    refetchInterval: auto ? 5000 : false, retry: false,
  });

  return (
    <>
      <PageHeader
        icon={Activity}
        title="System Health"
        description="Live status of the FastAPI backend, configured LLM and OpenAI credentials."
        actions={
          <>
            <div className="flex items-center gap-2 mr-2">
              <Switch checked={auto} onCheckedChange={setAuto} id="auto" />
              <Label htmlFor="auto" className="text-[11.5px]">Auto-refresh</Label>
            </div>
            <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} /> Refresh
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
        <StatCard label="API Status" value={data ? "Online" : error ? "Offline" : "—"} icon={data ? CheckCircle2 : XCircle} accent={data ? "success" : "destructive"} />
        <StatCard label="Version" value={data?.version || "—"} icon={Activity} />
        <StatCard label="Latency" value={latency !== null ? `${latency} ms` : "—"} icon={Activity} accent="muted" />
        <StatCard label="Categories" value={data?.categories_loaded ?? 0} icon={Activity} />
      </div>

      <Panel title="Detailed Status" description="All health probes">
        <div className="divide-y divide-border text-[12.5px]">
          <Row label="Backend URL" value={getBackendUrl()} />
          <Row label="Server" value={data?.status || (error ? "unreachable" : "—")} ok={data?.status === "ok"} />
          <Row label="Version" value={data?.version || "—"} />
          <Row label="LLM Model" value={data?.llm_model || "—"} />
          <Row label="OpenAI Key" value={data?.openai_key_configured ? "Configured" : "Missing"} ok={data?.openai_key_configured} />
          <Row label="Categories Loaded" value={String(data?.categories_loaded ?? "—")} />
        </div>
        {error && <div className="text-[12px] text-destructive mt-3">{(error as any).message}</div>}
      </Panel>
    </>
  );
}

function Row({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-mono flex items-center gap-1.5 ${ok === true ? "text-success" : ok === false ? "text-destructive" : ""}`}>
        {ok === true && <CheckCircle2 className="w-3 h-3" />}
        {ok === false && <XCircle className="w-3 h-3" />}
        {value}
      </span>
    </div>
  );
}
