// Offline compatibility launcher for managers predating the directory bundle.
// No downloader, scripts, process injection, antivirus exclusions or obfuscation.
// The embedded manifest is pinned at build time; every file is checked before use.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

internal static partial class Bootstrap {
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 2097152 };
    static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
    static string Hash(Stream stream) { using (var sha = SHA256.Create()) return Hex(sha.ComputeHash(stream)); }
    static string Safe(string value, string root = null) {
        string path = Path.GetFullPath(value);
        if (root != null && !path.StartsWith(Path.GetFullPath(root).TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Migration path escapes its directory");
        for (string current = path; current != null; current = Path.GetDirectoryName(current)) {
            if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Migration path contains a reparse point");
        }
        return path;
    }
    // Correct Windows argv quoting, including literal quotes and trailing slashes.
    static string Quote(string value) {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1).Append(c);
            else result.Append('\\', slashes).Append(c);
            slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }
    static void Extract(string destination) {
        using (var resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
        using (var archive = new ZipArchive(resource, ZipArchiveMode.Read)) {
            var manifest = archive.GetEntry("WindowsMCP.bundle.json");
            if (manifest == null || manifest.Length > 2097152) throw new IOException("Missing migration manifest");
            string text;
            using (var stream = manifest.Open()) {
                if (Hash(stream) != ManifestHash) throw new IOException("Migration manifest fingerprint mismatch");
            }
            using (var reader = new StreamReader(manifest.Open(), Encoding.UTF8)) text = reader.ReadToEnd();
            var record = Json.Deserialize<Dictionary<string, object>>(text);
            string contents = Convert.ToString(record["contents"]);
            if (Convert.ToInt32(record["schema"]) != 1 || !Regex.IsMatch(contents, "^_runtime-[a-f0-9]{20}$"))
                throw new IOException("Invalid migration support directory");
            var files = (Dictionary<string, object>)record["files"];
            if (files.Count < 2 || files.Count > 6000 || !files.ContainsKey("Windows-MCP.exe"))
                throw new IOException("Incomplete migration payload");
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase); long total = 0;
            foreach (var entry in archive.Entries) if (!names.Add(entry.FullName)) throw new IOException("Duplicate migration entry");
            if (names.Count != files.Count + 1) throw new IOException("Unexpected migration entries");
            // Validate the complete archive before writing any executable.
            foreach (var pair in files) {
                string name = pair.Key;
                if (name.Contains("\\") || name.Contains(":") || name.Split('/').Any(p => p == "" || p == "." || p == "..")
                    || (name != "Windows-MCP.exe" && !name.StartsWith(contents + "/", StringComparison.Ordinal)))
                    throw new IOException("Invalid migration entry path");
                Safe(Path.Combine(destination, name), destination);
                var expected = (Dictionary<string, object>)pair.Value;
                var entry = archive.GetEntry(name);
                if (entry == null || entry.Length != Convert.ToInt64(expected["size"])) throw new IOException("Migration entry size mismatch");
                total += entry.Length;
                if (total > 1500L * 1024 * 1024) throw new IOException("Migration payload exceeds the size limit");
                using (var stream = entry.Open()) if (Hash(stream) != Convert.ToString(expected["sha256"]))
                    throw new IOException("Migration entry fingerprint mismatch");
            }
            foreach (var pair in files) {
                string path = Safe(Path.Combine(destination, pair.Key), destination);
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                using (var input = archive.GetEntry(pair.Key).Open())
                using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) input.CopyTo(output);
                using (var copied = File.OpenRead(path))
                    if (Hash(copied) != Convert.ToString(((Dictionary<string, object>)pair.Value)["sha256"]))
                        throw new IOException("Copied migration entry failed verification");
            }
            File.WriteAllText(Path.Combine(destination, "WindowsMCP.bundle.json"), text, new UTF8Encoding(false));
        }
    }
    [STAThread]
    static int Main(string[] args) {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        string executable = Safe(Assembly.GetExecutingAssembly().Location);
        string parent = Path.GetDirectoryName(executable);
        string cache = Safe(Path.Combine(parent, "migration-payload-" + Guid.NewGuid().ToString("N")), parent);
        try {
            Directory.CreateDirectory(cache);
            Extract(cache);
            var info = new ProcessStartInfo(Path.Combine(cache, "Windows-MCP.exe")) {
                Arguments = Quote("--migrate-legacy=" + executable) + " " + String.Join(" ", args.Select(Quote)),
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = parent
            };
            foreach (string key in info.EnvironmentVariables.Keys.Cast<string>().ToArray())
                if (key.StartsWith("_PYI_", StringComparison.Ordinal) || key == "_MEIPASS2") info.EnvironmentVariables.Remove(key);
            info.EnvironmentVariables["PYINSTALLER_RESET_ENVIRONMENT"] = "1";
            using (var child = Process.Start(info)) { child.WaitForExit(); return child.ExitCode; }
        } catch (Exception error) {
            File.WriteAllText(Safe(Path.Combine(parent, "migration-error.json"), parent), Json.Serialize(new { error = error.Message }));
            return 1;
        } finally {
            // Only our newly created, unlaunched-after-exit cache is eligible.
            // Never follow links or delete another version's runtime directory.
            try {
                if (Directory.Exists(cache) && Directory.EnumerateFileSystemEntries(cache, "*", SearchOption.AllDirectories)
                    .All(p => (File.GetAttributes(p) & FileAttributes.ReparsePoint) == 0)) {
                    Safe(cache, parent);
                    Directory.Delete(cache, true);
                }
            } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }
}
