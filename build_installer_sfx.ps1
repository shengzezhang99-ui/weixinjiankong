param(
    [string]$DistDir = "",
    [string]$OutputDir = "installer"
)

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path -LiteralPath ".").Path
$appNameExpr = "[string]::Concat([char[]](0x5fae,0x4fe1,0x5f3a,0x63d0,0x9192,0x52a9,0x624b))"
$appName = Invoke-Expression $appNameExpr
if ([string]::IsNullOrWhiteSpace($DistDir)) {
    $DistDir = Join-Path "dist" $appName
}
$distPath = (Resolve-Path -LiteralPath $DistDir).Path
$exePath = Join-Path $distPath ($appName + ".exe")
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Packaged exe was not found: $exePath"
}

$stagePath = Join-Path $workspace "build\installer_sfx"
$outputPath = Join-Path $workspace $OutputDir
$resolvedBuildRoot = (Resolve-Path -LiteralPath "build").Path
if (-not $stagePath.StartsWith($resolvedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer staging directory is outside build: $stagePath"
}
if (Test-Path -LiteralPath $stagePath) {
    Remove-Item -LiteralPath $stagePath -Recurse -Force
}
New-Item -ItemType Directory -Path $stagePath, $outputPath -Force | Out-Null

$zipPath = Join-Path $stagePath "payload.zip"
Compress-Archive -Path (Join-Path $distPath "*") -DestinationPath $zipPath -Force

$sourcePath = Join-Path $stagePath "InstallerSfx.cs"
$source = @'
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

internal static class InstallerSfx
{
    private const string AppName = "\u5fae\u4fe1\u5f3a\u63d0\u9192\u52a9\u624b";
    private const string Magic = "WECHAT_ALERT_ASSISTANT_SFX_V1";

    [STAThread]
    private static int Main()
    {
        try
        {
            string selfPath = Process.GetCurrentProcess().MainModule.FileName;
            string tempZip = ExtractPayload(selfPath);
            string installDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                AppName);

            if (Directory.Exists(installDir))
            {
                Directory.Delete(installDir, true);
            }
            Directory.CreateDirectory(installDir);
            ZipFile.ExtractToDirectory(tempZip, installDir);
            TryDelete(tempZip);

            string appExe = Path.Combine(installDir, AppName + ".exe");
            CreateShortcut(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), AppName + ".lnk"),
                appExe,
                installDir);

            string programsDir = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
            Directory.CreateDirectory(programsDir);
            CreateShortcut(Path.Combine(programsDir, AppName + ".lnk"), appExe, installDir);

            MessageBox.Show("Installed to:\n" + installDir, AppName, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.ToString(), AppName + " installer failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static string ExtractPayload(string selfPath)
    {
        byte[] magic = Encoding.ASCII.GetBytes(Magic);
        using (FileStream input = File.OpenRead(selfPath))
        {
            if (input.Length < magic.Length + sizeof(long))
            {
                throw new InvalidDataException("Installer payload is missing.");
            }

            input.Seek(-magic.Length, SeekOrigin.End);
            byte[] actualMagic = ReadExactly(input, magic.Length);
            for (int i = 0; i < magic.Length; i++)
            {
                if (actualMagic[i] != magic[i])
                {
                    throw new InvalidDataException("Installer payload marker is invalid.");
                }
            }

            input.Seek(-(magic.Length + sizeof(long)), SeekOrigin.End);
            long zipLength = BitConverter.ToInt64(ReadExactly(input, sizeof(long)), 0);
            long zipStart = input.Length - magic.Length - sizeof(long) - zipLength;
            if (zipLength <= 0 || zipStart < 0)
            {
                throw new InvalidDataException("Installer payload length is invalid.");
            }

            string tempZip = Path.Combine(Path.GetTempPath(), "wechat-alert-assistant-" + Guid.NewGuid().ToString("N") + ".zip");
            input.Seek(zipStart, SeekOrigin.Begin);
            using (FileStream output = File.Create(tempZip))
            {
                CopyBytes(input, output, zipLength);
            }
            return tempZip;
        }
    }

    private static byte[] ReadExactly(Stream stream, int count)
    {
        byte[] buffer = new byte[count];
        int offset = 0;
        while (offset < count)
        {
            int read = stream.Read(buffer, offset, count - offset);
            if (read == 0)
            {
                throw new EndOfStreamException();
            }
            offset += read;
        }
        return buffer;
    }

    private static void CopyBytes(Stream input, Stream output, long count)
    {
        byte[] buffer = new byte[1024 * 1024];
        long remaining = count;
        while (remaining > 0)
        {
            int read = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
            if (read == 0)
            {
                throw new EndOfStreamException();
            }
            output.Write(buffer, 0, read);
            remaining -= read;
        }
    }

    private static void CreateShortcut(string shortcutPath, string targetPath, string workingDirectory)
    {
        Type shellType = Type.GetTypeFromProgID("WScript.Shell");
        if (shellType == null)
        {
            return;
        }
        dynamic shell = Activator.CreateInstance(shellType);
        dynamic shortcut = shell.CreateShortcut(shortcutPath);
        shortcut.TargetPath = targetPath;
        shortcut.WorkingDirectory = workingDirectory;
        shortcut.IconLocation = targetPath;
        shortcut.Save();
        Marshal.FinalReleaseComObject(shortcut);
        Marshal.FinalReleaseComObject(shell);
    }

    private static void TryDelete(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch
        {
        }
    }
}
'@
Set-Content -LiteralPath $sourcePath -Value $source -Encoding ASCII

$stubPath = Join-Path $stagePath "InstallerStub.exe"
$cscPath = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $cscPath)) {
    $cscPath = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $cscPath)) {
    throw "C# compiler was not found."
}

$iconPath = Join-Path $workspace "icon\app.ico"
$iconArg = @()
if (Test-Path -LiteralPath $iconPath) {
    $iconArg = @("/win32icon:$iconPath")
}
& $cscPath /nologo /target:winexe /optimize+ /out:$stubPath /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /r:System.Windows.Forms.dll /r:Microsoft.CSharp.dll @iconArg $sourcePath
if ($LASTEXITCODE -ne 0) {
    throw "C# installer stub build failed with exit code: $LASTEXITCODE"
}

$targetName = Join-Path $outputPath ($appName + "_Setup.exe")
if (Test-Path -LiteralPath $targetName) {
    Remove-Item -LiteralPath $targetName -Force
}
Copy-Item -LiteralPath $stubPath -Destination $targetName

$magicBytes = [System.Text.Encoding]::ASCII.GetBytes("WECHAT_ALERT_ASSISTANT_SFX_V1")
$zipLength = (Get-Item -LiteralPath $zipPath).Length
$lengthBytes = [System.BitConverter]::GetBytes([Int64]$zipLength)
$outputStream = [System.IO.File]::Open($targetName, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write)
try {
    $inputStream = [System.IO.File]::OpenRead($zipPath)
    try {
        $inputStream.CopyTo($outputStream)
    }
    finally {
        $inputStream.Dispose()
    }
    $outputStream.Write($lengthBytes, 0, $lengthBytes.Length)
    $outputStream.Write($magicBytes, 0, $magicBytes.Length)
}
finally {
    $outputStream.Dispose()
}

Get-Item -LiteralPath $targetName
