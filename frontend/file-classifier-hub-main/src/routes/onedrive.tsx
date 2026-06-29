import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Cloud, RefreshCw, Power, Play, FileText, HardDrive,
  CheckCircle2, XCircle, Search, Folder, ChevronRight, User, AlertCircle
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel, StatCard } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, getBackendUrl } from "@/lib/api";
import { useApp } from "@/lib/store";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { FolderPickerModal } from "@/components/FolderPickerModal";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle
} from "@/components/ui/dialog";

export const Route = createFileRoute("/onedrive")({ component: OneDrivePage });

function OneDrivePage() {
  const [activeMode, setActiveMode] = useState<"local" | "cloud">("local");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [maxPages, setMaxPages] = useState(3);
  const [model, setModel] = useState("gpt-4o");
  const [minScore, setMinScore] = useState(7.0);
  const [copyMode, setCopyMode] = useState(true);
  const [dryRun, setDryRun] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<any>(null);

  // Cloud OAuth States
  const [cloudInputId, setCloudInputId] = useState("");
  const [cloudInputName, setCloudInputName] = useState("");
  const [cloudOutputId, setCloudOutputId] = useState("");
  const [cloudOutputName, setCloudOutputName] = useState("");

  // Folder Picker States
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerTarget, setPickerTarget] = useState<"input" | "output" | null>(null);
  const [currentFolderId, setCurrentFolderId] = useState("root");
  const [folderStack, setFolderStack] = useState<{ id: string; name: string }[]>([{ id: "root", name: "Root" }]);
  const [selectedFolderId, setSelectedFolderId] = useState("");
  const [selectedFolderName, setSelectedFolderName] = useState("");

  // Local Folder Picker States
  const [localPickerOpen, setLocalPickerOpen] = useState(false);
  const [localPickerType, setLocalPickerType] = useState<"input" | "output">("input");

  const addLog = useApp((s) => s.addLog);
  const addActivity = useApp((s) => s.addActivity);

  // OneDrive Local Sync Status Query
  const status = useQuery({
    queryKey: ["onedrive-status", input],
    queryFn: () => api.onedriveStatus(input || undefined),
    enabled: activeMode === "local",
    retry: false,
  });

  // Microsoft OAuth Setup Query
  const setupStatus = useQuery({
    queryKey: ["onedrive-setup"],
    queryFn: api.onedriveCheckSetup,
    enabled: activeMode === "cloud",
    retry: false,
  });

  // Microsoft OAuth Profile Query
  const profile = useQuery({
    queryKey: ["onedrive-profile"],
    queryFn: api.onedriveProfile,
    enabled: activeMode === "cloud",
    retry: false,
  });

  // OneDrive Cloud Folders list inside Picker Dialog
  const folders = useQuery({
    queryKey: ["onedrive-folders", currentFolderId],
    queryFn: () => api.onedriveFolders(currentFolderId),
    enabled: activeMode === "cloud" && pickerOpen,
    retry: false,
  });

  async function runLocal() {
    setRunning(true);
    try {
      const r = await api.onedriveClassify({
        onedrive_input_folder: input || undefined,
        onedrive_output_folder: output || undefined,
        pdf_max_pages: maxPages,
        min_score: minScore,
        llm_model: model,
        copy_mode: copyMode,
        dry_run: dryRun,
      });
      setResult(r);
      addActivity({ kind: "drive", title: "OneDrive Local classification done", detail: JSON.stringify(r).slice(0, 80) });
      addLog("INFO", "drive", "OneDrive Local classification done", r);
      toast.success("OneDrive Local classification complete");
      status.refetch();
    } catch (e: any) {
      toast.error(e.message);
      addLog("ERROR", "drive", e.message);
    } finally {
      setRunning(false);
    }
  }

  async function runCloud() {
    if (!cloudInputId || !cloudOutputId) {
      return toast.error("Please select both input and output folders.");
    }
    setRunning(true);
    try {
      const r = await api.onedriveCloudClassify({
        onedrive_input_folder_id: cloudInputId,
        onedrive_output_folder_id: cloudOutputId,
        pdf_max_pages: maxPages,
        min_score: minScore,
        llm_model: model,
        copy_mode: copyMode,
        dry_run: dryRun,
      });
      setResult(r);
      addActivity({ kind: "drive", title: "OneDrive Cloud classification done", detail: JSON.stringify(r).slice(0, 80) });
      addLog("INFO", "drive", "OneDrive Cloud classification done", r);
      toast.success("OneDrive Cloud classification complete");
    } catch (e: any) {
      toast.error(e.message);
      addLog("ERROR", "drive", e.message);
    } finally {
      setRunning(false);
    }
  }

  const handleLogin = () => {
    window.location.href = `${getBackendUrl()}/onedrive/login`;
  };

  const handleLogout = () => {
    window.location.href = `${getBackendUrl()}/onedrive/logout`;
  };

  // Folder Picker Navigation
  const openPicker = (target: "input" | "output") => {
    setPickerTarget(target);
    setCurrentFolderId("root");
    setFolderStack([{ id: "root", name: "Root" }]);
    setSelectedFolderId("");
    setSelectedFolderName("");
    setPickerOpen(true);
  };

  const handleSelectFolder = (id: string, name: string) => {
    setSelectedFolderId(id);
    setSelectedFolderName(name);
  };

  const handleEnterFolder = (id: string, name: string) => {
    setFolderStack((prev) => [...prev, { id, name }]);
    setCurrentFolderId(id);
    setSelectedFolderId("");
    setSelectedFolderName("");
  };

  const handleNavigateStack = (idx: number) => {
    const target = folderStack[idx];
    setFolderStack((prev) => prev.slice(0, idx + 1));
    setCurrentFolderId(target.id);
    setSelectedFolderId("");
    setSelectedFolderName("");
  };

  const handleConfirmFolder = () => {
    if (pickerTarget === "input") {
      setCloudInputId(selectedFolderId);
      setCloudInputName(selectedFolderName);
    } else {
      setCloudOutputId(selectedFolderId);
      setCloudOutputName(selectedFolderName);
    }
    setPickerOpen(false);
  };

  const selectLocalFolder = (type: "input" | "output") => {
    setLocalPickerType(type);
    setLocalPickerOpen(true);
  };

  const handleLocalFolderSelect = (path: string) => {
    if (localPickerType === "input") {
      setInput(path);
    } else {
      setOutput(path);
    }
  };

  const s = status.data;

  return (
    <>
      <PageHeader
        icon={Cloud}
        title="OneDrive"
        description="Classify PDFs directly from a OneDrive folder synchronized on the backend host or via OneDrive Cloud OAuth."
        actions={
          <>
            <Button size="sm" variant="outline" onClick={() => activeMode === "local" ? status.refetch() : profile.refetch()}>
              <RefreshCw className="w-3.5 h-3.5" /> Refresh
            </Button>
            {activeMode === "cloud" && profile.data?.authenticated && (
              <Button size="sm" variant="destructive" onClick={handleLogout}>
                <Power className="w-3.5 h-3.5" /> Sign Out
              </Button>
            )}
          </>
        }
      />

      <div className="flex gap-2 mb-4 bg-muted/60 p-1 rounded-md max-w-[340px] border border-border">
        <button
          onClick={() => { setActiveMode("local"); setResult(null); }}
          className={cn(
            "flex-1 text-[12px] font-medium py-1.5 rounded-sm transition-all text-center",
            activeMode === "local"
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          Local Sync Folder
        </button>
        <button
          onClick={() => { setActiveMode("cloud"); setResult(null); }}
          className={cn(
            "flex-1 text-[12px] font-medium py-1.5 rounded-sm transition-all text-center",
            activeMode === "cloud"
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          Cloud Account (OAuth)
        </button>
      </div>

      {activeMode === "local" ? (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
            <StatCard
              label="Connection"
              value={s?.connected ? "Connected" : status.isError ? "Error" : "Checking…"}
              icon={s?.connected ? CheckCircle2 : XCircle}
              accent={s?.connected ? "success" : "destructive"}
            />
            <StatCard label="PDFs Ready" value={s?.pdf_count ?? 0} icon={FileText} />
            <StatCard
              label="OneDrive Root"
              value={s?.onedrive_root ? "Mounted" : "—"}
              icon={HardDrive}
              hint={s?.onedrive_root || undefined}
              accent={s?.onedrive_root ? "success" : "muted"}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <Panel title="OneDrive Sync Folders" description="Override defaults from .env">
              <div className="space-y-3">
                <div>
                  <Label className="text-[11.5px]">Input Folder</Label>
                  <div className="flex gap-2 items-center mt-1">
                    <Input value={input} onChange={(e) => setInput(e.target.value)} className="h-8 font-mono text-[12px] flex-1" placeholder={s?.onedrive_input || "C:\\Users\\Intern\\OneDrive\\uploads"} />
                    <Button size="icon" variant="outline" className="h-8 w-8 shrink-0 shadow-sm hover:bg-accent" onClick={() => selectLocalFolder("input")}>
                      <Search className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                <div>
                  <Label className="text-[11.5px]">Output Folder</Label>
                  <div className="flex gap-2 items-center mt-1">
                    <Input value={output} onChange={(e) => setOutput(e.target.value)} className="h-8 font-mono text-[12px] flex-1" placeholder={s?.onedrive_output || "C:\\Users\\Intern\\OneDrive\\sorted"} />
                    <Button size="icon" variant="outline" className="h-8 w-8 shrink-0 shadow-sm hover:bg-accent" onClick={() => selectLocalFolder("output")}>
                      <Search className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <Label className="text-[11.5px]">Max Pages</Label>
                    <Input type="number" min={1} max={20} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} className="h-8 mt-1" />
                  </div>
                  <div>
                    <Label className="text-[11.5px]">LLM Model</Label>
                    <Input value={model} onChange={(e) => setModel(e.target.value)} className="h-8 mt-1" />
                  </div>
                  <div>
                    <Label className="text-[11.5px]">Min Score</Label>
                    <Input type="number" min={0} max={10} step={0.1} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="h-8 mt-1" />
                  </div>
                </div>
                <div className="flex gap-6">
                  <div className="flex items-center gap-2"><Switch checked={copyMode} onCheckedChange={setCopyMode} /><Label className="text-[11.5px]">Copy Mode</Label></div>
                  <div className="flex items-center gap-2"><Switch checked={dryRun} onCheckedChange={setDryRun} /><Label className="text-[11.5px]">Dry Run</Label></div>
                </div>
                <div className="flex justify-end">
                  <Button size="sm" onClick={runLocal} disabled={running || !s?.connected}>
                    <Play className="w-3.5 h-3.5" /> {running ? "Running…" : "Run on Local OneDrive"}
                  </Button>
                </div>
              </div>
            </Panel>

            <Panel title="Files in OneDrive" description={`${s?.pdf_count ?? 0} PDFs ready`}>
              {!s || s.pdf_files.length === 0 ? (
                <div className="text-[12.5px] text-muted-foreground text-center py-8">No PDFs found.</div>
              ) : (
                <ul className="divide-y divide-border max-h-72 overflow-auto">
                  {s.pdf_files.map((f, i) => (
                    <li key={i} className="flex items-center gap-2 py-2 text-[12.5px]">
                      <FileText className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                      <span className="truncate font-mono">{f}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <StatCard
              label="OneDrive Cloud Setup"
              value={setupStatus.data?.oauth_configured ? "Configured" : "Missing Config"}
              icon={setupStatus.data?.oauth_configured ? CheckCircle2 : XCircle}
              accent={setupStatus.data?.oauth_configured ? "success" : "destructive"}
            />
            <StatCard
              label="Account Connection"
              value={profile.data?.authenticated ? "Connected" : "Disconnected"}
              icon={profile.data?.authenticated ? CheckCircle2 : XCircle}
              accent={profile.data?.authenticated ? "success" : "muted"}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {!profile.data?.authenticated ? (
              <Panel title="OneDrive Cloud Connection" description="Connect your Microsoft Account using OAuth" className="lg:col-span-2">
                {!setupStatus.data?.oauth_configured && setupStatus.data?.message && (
                  <div className="bg-destructive/10 border border-destructive/25 text-destructive rounded-md p-3 text-[12px] leading-relaxed mb-3 flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-destructive" />
                    <div>
                      <strong>Server Configuration Required:</strong> Microsoft OAuth credentials (MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET) are missing on the server. Please configure them in your .env file to enable OneDrive OAuth.
                    </div>
                  </div>
                )}
                <div className="flex flex-col items-center justify-center py-8 text-center">
                  <Cloud className="w-12 h-12 text-muted-foreground/40 mb-3" />
                  <div className="text-[13px] font-semibold mb-1">Not signed in to OneDrive Cloud</div>
                  <p className="text-[12px] text-muted-foreground max-w-sm mb-4 leading-relaxed">
                    Authenticate with your Microsoft Account to browse Cloud OneDrive directories and run classifications directly in the cloud.
                  </p>
                  <Button onClick={handleLogin} disabled={!setupStatus.data?.oauth_configured} className="gap-2">
                    <Power className="w-3.5 h-3.5" /> Sign in with Microsoft
                  </Button>
                </div>
              </Panel>
            ) : (
              <Panel title="OneDrive Cloud Connection" description="Currently signed-in account details">
                <div className="flex flex-col sm:flex-row items-center gap-3 p-3 rounded-lg border border-border bg-muted/20 justify-between">
                  <div className="flex items-center gap-3 min-w-0 w-full sm:w-auto">
                    <div className="w-10 h-10 rounded-full overflow-hidden border border-border bg-background shrink-0 flex items-center justify-center">
                      {profile.data?.picture ? (
                        <img src={profile.data.picture} alt={profile.data.name || "Profile"} className="w-full h-full object-cover" />
                      ) : (
                        <User className="w-5 h-5 text-muted-foreground" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <div className="text-[12.5px] font-semibold truncate text-foreground">{profile.data?.name || "Connected User"}</div>
                      <div className="text-[11px] text-muted-foreground truncate">{profile.data?.email || "Connected via Microsoft"}</div>
                    </div>
                  </div>
                  <div className="flex gap-1.5 shrink-0 w-full sm:w-auto justify-end mt-2 sm:mt-0">
                    <Button size="sm" variant="outline" onClick={handleLogin} className="text-[11px] h-7 px-2.5">
                      Switch Account
                    </Button>
                    <Button size="sm" variant="destructive" onClick={handleLogout} className="text-[11px] h-7 px-2.5">
                      Sign Out
                    </Button>
                  </div>
                </div>
              </Panel>
            )}

            {profile.data?.authenticated && (
              <Panel title="OneDrive Cloud Folders" description="Configure input/output folders and classification options">
                <div className="space-y-3">
                  <div>
                    <Label className="text-[11.5px]">Input Folder</Label>
                    <div className="flex gap-1.5 mt-1">
                      <Input value={cloudInputName ? `📁 ${cloudInputName} (ID: ${cloudInputId.slice(0, 8)}...)` : ""} readOnly placeholder="Choose OneDrive input folder..." className="h-8 font-mono text-[11.5px] bg-muted/30 cursor-pointer flex-1" onClick={() => openPicker("input")} />
                      <Button size="sm" variant="outline" className="h-8 w-8 p-0" onClick={() => openPicker("input")}>
                        <Search className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                  <div>
                    <Label className="text-[11.5px]">Output Folder</Label>
                    <div className="flex gap-1.5 mt-1">
                      <Input value={cloudOutputName ? `📁 ${cloudOutputName} (ID: ${cloudOutputId.slice(0, 8)}...)` : ""} readOnly placeholder="Choose OneDrive output folder..." className="h-8 font-mono text-[11.5px] bg-muted/30 cursor-pointer flex-1" onClick={() => openPicker("output")} />
                      <Button size="sm" variant="outline" className="h-8 w-8 p-0" onClick={() => openPicker("output")}>
                        <Search className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <Label className="text-[11.5px]">Max Pages</Label>
                      <Input type="number" min={1} max={20} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} className="h-8 mt-1" />
                    </div>
                    <div>
                      <Label className="text-[11.5px]">LLM Model</Label>
                      <Input value={model} onChange={(e) => setModel(e.target.value)} className="h-8 mt-1" />
                    </div>
                    <div>
                      <Label className="text-[11.5px]">Min Score</Label>
                      <Input type="number" min={0} max={10} step={0.1} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="h-8 mt-1" />
                    </div>
                  </div>
                  <div className="flex gap-6">
                    <div className="flex items-center gap-2"><Switch checked={copyMode} onCheckedChange={setCopyMode} /><Label className="text-[11.5px]">Copy Mode (keep originals)</Label></div>
                    <div className="flex items-center gap-2"><Switch checked={dryRun} onCheckedChange={setDryRun} /><Label className="text-[11.5px]">Dry Run (classify only)</Label></div>
                  </div>
                  <div className="flex justify-end">
                    <Button size="sm" onClick={runCloud} disabled={running || !cloudInputId || !cloudOutputId}>
                      <Play className="w-3.5 h-3.5" /> {running ? "Running…" : "Run on OneDrive Cloud"}
                    </Button>
                  </div>
                </div>
              </Panel>
            )}
          </div>
        </>
      )}

      {result && (
        <Panel title="Last Run Result" className="mt-3">
          <pre className="text-[11.5px] font-mono whitespace-pre-wrap bg-muted/40 rounded-md p-3 max-h-72 overflow-auto">{JSON.stringify(result, null, 2)}</pre>
        </Panel>
      )}

      {/* Folder Picker Modal Dialog */}
      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Select OneDrive Folder</DialogTitle>
            <DialogDescription>
              Choose a folder as the {pickerTarget === "input" ? "input source" : "output destination"}.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            {/* Breadcrumbs stack */}
            <div className="flex items-center flex-wrap gap-1 text-[11px] font-mono bg-muted/50 p-2 rounded border border-border">
              {folderStack.map((item, idx) => (
                <span key={item.id} className="flex items-center gap-1">
                  {idx > 0 && <ChevronRight className="w-3 h-3 text-muted-foreground" />}
                  <button
                    onClick={() => handleNavigateStack(idx)}
                    className="hover:underline hover:text-foreground text-muted-foreground font-semibold"
                  >
                    {item.name}
                  </button>
                </span>
              ))}
            </div>

            {/* Folder list container */}
            <div className="border border-border rounded-lg bg-background max-h-60 overflow-y-auto divide-y divide-border min-h-[160px]">
              {folders.isLoading ? (
                <div className="text-[12px] text-muted-foreground text-center py-12">Loading folders...</div>
              ) : folders.isError ? (
                <div className="text-[12px] text-destructive text-center py-12">Failed to load folders.</div>
              ) : !folders.data?.folders || folders.data.folders.length === 0 ? (
                <div className="text-[12px] text-muted-foreground text-center py-12">No folders found inside this directory.</div>
              ) : (
                folders.data.folders.map((folder) => {
                  const isSelected = selectedFolderId === folder.id;
                  return (
                    <button
                      key={folder.id}
                      onClick={() => handleSelectFolder(folder.id, folder.name)}
                      onDoubleClick={() => handleEnterFolder(folder.id, folder.name)}
                      className={cn(
                        "w-full text-left px-3 py-2 text-[12px] hover:bg-muted flex items-center gap-2 transition-colors",
                        isSelected && "bg-primary/10 text-primary font-medium"
                      )}
                    >
                      <Folder className={cn("w-4 h-4 shrink-0 text-muted-foreground", isSelected && "text-primary")} />
                      <span className="truncate">{folder.name}</span>
                    </button>
                  );
                })
              )}
            </div>
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(false)}>Cancel</Button>
            <Button
              size="sm"
              onClick={handleConfirmFolder}
              disabled={!selectedFolderId}
            >
              Select Folder
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <FolderPickerModal
        open={localPickerOpen}
        onClose={() => setLocalPickerOpen(false)}
        onSelect={handleLocalFolderSelect}
        title={localPickerType === "input" ? "Select Input Folder" : "Select Output Folder"}
        initialPath={localPickerType === "input" ? input : output}
      />
    </>
  );
}
