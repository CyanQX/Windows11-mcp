// Isolated WPF TextPattern provider. No user documents, profiles or network access.
using System;
using System.IO;
using System.Diagnostics;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Threading;

public static class TextFixture {
    [STAThread]
    public static void Main(string[] args) {
        var app = new Application();
        var panel = new StackPanel { Margin = new Thickness(20) };
        var text = new TextBox { AcceptsReturn = true, Height = 180, Width = 500,
                                 Text = "one two one", FontSize = 20 };
        AutomationProperties.SetName(text, "Range editor");
        AutomationProperties.SetAutomationId(text, "range-editor");
        panel.Children.Add(text);
        var window = new Window { Title = args[1], Content = panel, Width = 600,
                                  Height = 300, Left = 150, Top = 150, Topmost = true };
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(120) };
        timer.Tick += (s, e) => { timer.Stop(); window.Close(); };
        window.ContentRendered += (s, e) => {
            var handle = new WindowInteropHelper(window).Handle.ToInt64();
            File.WriteAllText(args[0], handle.ToString());
            timer.Start();
        };
        app.Run(window);
    }
}
