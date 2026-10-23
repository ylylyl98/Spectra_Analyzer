using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

internal static class Launcher
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    // CommandLineToArgvW / Python rules: double backslashes before quotes and
    // the closing quote. Always quote, including empty strings and Unicode paths.
    internal static string QuoteArgument(string argument)
    {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char value in argument)
        {
            if (value == '\\') { slashes++; continue; }
            if (value == '"')
                result.Append('\\', slashes * 2 + 1);
            else
                result.Append('\\', slashes);
            result.Append(value);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] arguments)
    {
        try
        {
            Marshal.ThrowExceptionForHR(SetCurrentProcessExplicitAppUserModelID(BuildSettings.AppId));
            string python = Path.Combine(BuildSettings.Repository, @".venv\Scripts\pythonw.exe");
            string entry = Path.Combine(BuildSettings.Repository, "run_qt.py");
            string bootstrap = Path.Combine(BuildSettings.Repository, @"tools\windows\bootstrap.py");
            foreach (string path in new string[] { python, entry, bootstrap })
                if (!File.Exists(path)) throw new FileNotFoundException("Required app file is missing", path);

            string log = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, @"logs\startup.log");
            StringBuilder command = new StringBuilder();
            foreach (string value in new string[] { "-X", "utf8", bootstrap, entry, log })
                command.Append(QuoteArgument(value)).Append(' ');
            foreach (string value in arguments)
                command.Append(QuoteArgument(value)).Append(' ');
            ProcessStartInfo start = new ProcessStartInfo(python, command.ToString());
            start.WorkingDirectory = BuildSettings.Repository;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process process = Process.Start(start))
            {
                if (process == null) throw new InvalidOperationException("Windows did not start the application.");
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message + "\n\nCheck that the source folder and .venv still exist. " +
                "If you moved the source folder, rerun tools\\windows\\Install-Launcher.ps1 there.",
                "Spectra Analyzer could not start", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
