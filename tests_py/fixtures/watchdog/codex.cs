// Offline executable fixture. Never contacts a provider or reads credentials.
using System;
using System.IO;
using System.Diagnostics;
using System.Threading;

class OfflineCodex {
    static int Main(string[] args) {
        if (Array.IndexOf(args, "--version") >= 0) { Console.WriteLine("codex-cli 0.149.0"); return 0; }
        if (Array.IndexOf(args, "--help") >= 0) {
            Console.WriteLine("--ask-for-approval --sandbox --cd --config --disable --ephemeral --ignore-user-config --ignore-rules --strict-config --skip-git-repo-check --json");
            return 0;
        }
        if (args.Length == 2 && args[0] == "login" && args[1] == "status") {
            Console.WriteLine("Offline fixture authentication; not a provider session"); return 0;
        }
        bool child = args.Length == 2 && args[0] == "--writer-child";
        int cd = Array.IndexOf(args, "--cd");
        if (!child && (cd < 0 || Array.IndexOf(args, "exec") < 0)) return 2;
        string workspace = child ? args[1] : args[cd + 1];
        Process descendant = child ? null : Process.Start(new ProcessStartInfo(Environment.GetCommandLineArgs()[0], "--writer-child \"" + workspace + "\"") { UseShellExecute = false });
        string log = Path.Combine(workspace, "writer-" + Process.GetCurrentProcess().Id + ".log");
        DateTime deadline = DateTime.UtcNow.AddSeconds(45);
        while (!File.Exists(Path.Combine(workspace, "finish.flag"))) {
            if (DateTime.UtcNow >= deadline) return 3;
            File.AppendAllText(log, "offline\n");
            Thread.Sleep(50);
        }
        if (!child) {
            if (!descendant.WaitForExit(2000)) return 4;
            Console.WriteLine("{\"type\":\"thread.started\",\"thread_id\":\"offline-fixture\"}");
            Console.WriteLine("{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"AIDO_READ_ONLY_SMOKE_OK\"}}");
            Console.WriteLine("{\"type\":\"turn.completed\"}");
        }
        return 0;
    }
}
