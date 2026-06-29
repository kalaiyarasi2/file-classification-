import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api } from "@/lib/api";
import {
  Folder,
  FolderOpen,
  ArrowUp,
  HardDrive,
  RefreshCw,
  AlertCircle,
  FolderClosed,
} from "lucide-react";
import { toast } from "sonner";

interface FolderPickerModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
  title?: string;
  initialPath?: string;
}

export function FolderPickerModal({
  open,
  onClose,
  onSelect,
  title = "Select Folder",
  initialPath = "",
}: FolderPickerModalProps) {
  const [currentPath, setCurrentPath] = useState(initialPath);
  const [inputPath, setInputPath] = useState(initialPath);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [subdirs, setSubdirs] = useState<{ name: string; path: string }[]>([]);
  const [drives, setDrives] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDirs = async (path?: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listDirectories(path);
      setCurrentPath(res.current_path);
      setInputPath(res.current_path);
      setParentPath(res.parent_path);
      setSubdirs(res.subdirectories);
      setDrives(res.drives || []);
    } catch (e: any) {
      console.error(e);
      setError(e.message || "Failed to read directory");
      toast.error("Error reading directory: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Fetch initial folder list when modal is opened
  useEffect(() => {
    if (open) {
      fetchDirs(initialPath);
    }
  }, [open, initialPath]);

  const handleNavigate = (path: string) => {
    fetchDirs(path);
  };

  const handleGoClick = () => {
    fetchDirs(inputPath);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleGoClick();
    }
  };

  return (
    <Dialog open={open} onOpenChange={(val) => !val && onClose()}>
      <DialogContent className="sm:max-w-[550px] max-h-[85vh] flex flex-col p-5 gap-4">
        <DialogHeader className="pb-1 border-b">
          <DialogTitle className="text-base font-semibold flex items-center gap-2 text-foreground">
            <FolderOpen className="w-5 h-5 text-primary" />
            {title}
          </DialogTitle>
        </DialogHeader>

        {/* Address bar */}
        <div className="flex gap-2 items-center">
          <Input
            value={inputPath}
            onChange={(e) => setInputPath(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Absolute folder path (e.g. C:\data\incoming or /data)"
            className="h-9 font-mono text-[12.5px] flex-1"
          />
          <Button
            size="icon"
            variant="outline"
            className="h-9 w-9 shrink-0"
            onClick={handleGoClick}
            disabled={loading}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>

        {/* Quick Access Drives (Windows-only list) */}
        {drives.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
              <HardDrive className="w-3.5 h-3.5" /> Drives
            </span>
            <div className="flex flex-wrap gap-1.5">
              {drives.map((drive) => (
                <Button
                  key={drive}
                  size="sm"
                  variant="secondary"
                  className="h-7 px-2.5 font-mono text-[11.5px] bg-muted hover:bg-accent"
                  onClick={() => handleNavigate(drive)}
                >
                  {drive}
                </Button>
              ))}
            </div>
          </div>
        )}

        {/* Folder contents explorer */}
        <div className="flex-1 min-h-[220px] rounded-md border bg-card flex flex-col overflow-hidden">
          {error ? (
            <div className="flex-1 flex flex-col items-center justify-center p-6 text-center gap-2">
              <AlertCircle className="w-8 h-8 text-destructive" />
              <p className="text-[13px] font-medium text-destructive">Error Accessing Path</p>
              <p className="text-[11.5px] text-muted-foreground max-w-sm">{error}</p>
              <Button
                size="sm"
                variant="outline"
                className="mt-2 text-xs"
                onClick={() => fetchDirs()}
              >
                Go to Workspace Root
              </Button>
            </div>
          ) : (
            <ScrollArea className="flex-1 p-2">
              <div className="space-y-1">
                {/* Parent directory navigation item */}
                {parentPath && (
                  <button
                    onClick={() => handleNavigate(parentPath)}
                    className="w-full flex items-center gap-2.5 px-2.5 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground hover:bg-accent rounded transition-colors text-left"
                  >
                    <ArrowUp className="w-4 h-4 shrink-0 text-muted-foreground" />
                    <span className="font-semibold">.. (Go Up)</span>
                  </button>
                )}

                {/* Subdirectories */}
                {subdirs.length === 0 ? (
                  <div className="text-center py-10 text-[12px] text-muted-foreground flex flex-col items-center gap-1.5">
                    <FolderClosed className="w-8 h-8 opacity-40" />
                    No subdirectories found
                  </div>
                ) : (
                  subdirs.map((dir) => (
                    <button
                      key={dir.path}
                      onClick={() => handleNavigate(dir.path)}
                      className="w-full flex items-center gap-2.5 px-2.5 py-1.5 text-[12.5px] text-foreground hover:bg-accent rounded transition-colors text-left font-mono"
                    >
                      <Folder className="w-4 h-4 shrink-0 text-amber-500 fill-amber-500/20" />
                      <span>{dir.name}</span>
                    </button>
                  ))
                )}
              </div>
            </ScrollArea>
          )}
        </div>

        {/* Selected path and action buttons */}
        <div className="text-[12px] text-muted-foreground break-all bg-muted/40 p-2 rounded border font-mono">
          <span className="font-semibold text-[11px] text-foreground block mb-0.5">Selected:</span>
          {currentPath}
        </div>

        <DialogFooter className="flex justify-end gap-2 border-t pt-3">
          <Button variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => {
              onSelect(currentPath);
              onClose();
            }}
            disabled={loading || !!error}
          >
            Select Folder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
