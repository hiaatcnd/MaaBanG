using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Presenters;
using Avalonia.Controls.Primitives;
using Avalonia.Headless;
using Avalonia.Input;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Avalonia.VisualTree;
using FluentAvalonia.Styling;
using SukiUI;
using SukiUI.Extensions;

// Headless regression test for the packaged song-search ComboBox behaviour:
// searching must filter ItemsSource (keeping the default virtualizing panel and
// focus in the search box), spaces must be typable, the selected item must stay
// pinned/settled while filtering, and closing/clearing must restore the original
// ItemsSource instance and selection.
internal sealed class SongItem
{
    public string DisplayName { get; set; } = "";
    public string Label { get; set; } = "";
    public override string ToString() => DisplayName;
}

internal static class Program
{
    private static Window _window = null!;
    private static int _failures;

    private static void Pump(int frames = 0)
    {
        Dispatcher.UIThread.RunJobs();
        for (var i = 0; i < frames; i++)
        {
            AvaloniaHeadlessPlatform.ForceRenderTimerTick(1);
            Dispatcher.UIThread.RunJobs();
        }
    }

    private static void Check(bool condition, string message)
    {
        Console.WriteLine((condition ? "[PASS] " : "[FAIL] ") + message);
        if (!condition) _failures++;
    }

    private static T? FindDescendant<T>(Visual root) where T : Visual =>
        root.GetVisualDescendants().OfType<T>().FirstOrDefault();

    private static Popup? FindPopup(ComboBox combo) => FindDescendant<Popup>(combo);

    private static ItemsPresenter? FindPresenter(ComboBox combo)
    {
        var popup = FindPopup(combo);
        return popup?.Child is Visual visual
            ? visual.GetVisualDescendants().OfType<ItemsPresenter>().FirstOrDefault()
            : null;
    }

    private static Panel? FindItemsPanel(ComboBox combo) =>
        FindPresenter(combo)?.GetVisualChildren().OfType<Panel>().FirstOrDefault();

    private static TextBox? FindSearchBox(ComboBox combo)
    {
        var popup = FindPopup(combo);
        if (popup?.Child is not Visual visual) return null;
        return visual.GetVisualDescendants().OfType<TextBox>().FirstOrDefault(t => t.Name == "PART_SearchBox");
    }

    private static int RealizedCount(ComboBox combo)
    {
        var popup = FindPopup(combo);
        if (popup?.Child is not Visual visual) return -1;
        return visual.GetVisualDescendants().OfType<ComboBoxItem>().Count();
    }

    private static int SourceCount(ComboBox combo) =>
        combo.ItemsSource is IEnumerable<object> source ? source.Count() : -1;

    private static string FocusedDesc()
    {
        var focused = TopLevel.GetTopLevel(_window)?.FocusManager?.GetFocusedElement();
        if (focused == null) return "null";
        if (focused is Visual visual) return visual.GetType().Name + (string.IsNullOrEmpty(visual.Name) ? "" : "#" + visual.Name);
        return focused.GetType().Name;
    }

    private static void TypeChar(char c)
    {
        var key = char.IsLetter(c) ? (Key)Enum.Parse(typeof(Key), char.ToUpperInvariant(c).ToString()) : Key.Space;
        _window.KeyPress(key, RawInputModifiers.None);
        _window.KeyTextInput(c.ToString());
        _window.KeyRelease(key, RawInputModifiers.None);
        Dispatcher.UIThread.RunJobs();
    }

    private static void TypeText(string text)
    {
        foreach (var c in text) TypeChar(c);
        Dispatcher.UIThread.RunJobs();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        var root = args.Length > 0 ? Path.GetFullPath(args[0]) : Directory.GetCurrentDirectory();
        Console.WriteLine($"[env] packaged app directory: {root}");
        Check(Directory.Exists(root), $"packaged app directory exists (path=\"{root}\")");
        if (!Directory.Exists(root))
        {
            Console.WriteLine(_failures == 0 ? "[RESULT] ALL CHECKS PASSED" : $"[RESULT] {_failures} CHECK(S) FAILED");
            Environment.Exit(_failures == 0 ? 0 : 1);
        }
        var packageSuki = Path.Combine(root, "libs", "SukiUI.dll");
        Console.WriteLine($"[env] packaged SukiUI.dll: exists={File.Exists(packageSuki)} path=\"{packageSuki}\"");
        Console.WriteLine($"[env] avalonia version: {typeof(Window).Assembly.GetName().Version}");

        try
        {
            AppBuilder.Configure<Application>()
                .UseSkia()
                .UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false })
                .SetupWithoutStarting();
            Application.Current!.Styles.Add(new FluentTheme());
            Application.Current.Styles.Add(new FluentAvaloniaTheme());
            Application.Current.Styles.Add(new SukiTheme());

            // Same shape as the packaged song picker: a large list, an item whose
            // display name is searchable, a distinct Label fallback property and a
            // selected item that does not match typical queries.
            var items = Enumerable.Range(0, 736)
                .Select(i => new SongItem
                {
                    DisplayName = i == 0 ? "SAVIOR OF SONG" : $"Song {i:000} Alpha Beta",
                    Label = $"Band {i % 10}",
                })
                .ToList();

            var combo = new SearchComboBox { ItemsSource = items, SelectedIndex = 10, Width = 480, MaxDropDownHeight = 480 };
            ComboBoxExtensions.SetCanSearch(combo, true);
            ComboBoxExtensions.SetSearchMemberPath(combo, "DisplayName");
            ComboBoxExtensions.SetSearchWatermark(combo, "搜索...");
            ComboBoxExtensions.SetDisableNavigationOnLostFocus(combo, true);

            _window = new Window { Width = 760, Height = 560, Content = combo };
            _window.Show();
            Pump(5);

            var selectedOriginal = combo.SelectedItem as SongItem;
            Check(selectedOriginal != null && selectedOriginal.DisplayName == "Song 010 Alpha Beta",
                $"selected item starts as Song 010 (selected={(combo.SelectedItem as SongItem)?.DisplayName ?? "null"})");

            var selectionEvents = new List<string>();
            combo.SelectionChanged += (_, _) =>
            {
                var selected = combo.SelectedItem as SongItem;
                selectionEvents.Add($"idx={combo.SelectedIndex} item={(selected?.DisplayName ?? "null")}");
            };

            // ---- A. Opening the dropdown keeps virtualization and all 736 items. ----
            var openWatch = Stopwatch.StartNew();
            combo.IsDropDownOpen = true;
            Pump(30);
            openWatch.Stop();

            var panel = FindItemsPanel(combo);
            var searchBox = FindSearchBox(combo);
            var realized = RealizedCount(combo);
            var initialCount = SourceCount(combo);
            Console.WriteLine($"[open] open={combo.IsDropDownOpen} searchBox={searchBox != null} panel={panel?.GetType().Name ?? "null"} realized={realized} source={initialCount} focused={FocusedDesc()} openCost={openWatch.Elapsed.TotalMilliseconds:F1}ms");

            Check(searchBox != null, "search box is present in the popup");
            Check(panel != null && panel.GetType().Name.Contains("Virtualizing"),
                $"ItemsPanel stays virtualizing (panel={panel?.GetType().Name ?? "null"})");
            Check(initialCount == 736, $"ItemsSource initially has all 736 songs (source={initialCount})");
            Check(realized > 0 && realized < 100, $"only a small number of containers is realized (realized={realized})");
            Check(searchBox?.IsFocused == true, "search box receives focus when the dropdown opens");
            if (searchBox == null)
            {
                Console.WriteLine($"[RESULT] {_failures} CHECK(S) FAILED");
                Environment.Exit(1);
            }

            // ---- B0. Space KeyDown must stay unhandled. Avalonia's Win32 backend
            // suppresses the TextInput event once KeyDown is handled, so marking the
            // space as handled keeps the dropdown open but silently drops the
            // character on a real keyboard. KeyTextInput() below cannot catch that,
            // so assert the routed event stays unhandled here.
            var spaceDown = new KeyEventArgs { RoutedEvent = InputElement.KeyDownEvent, Key = Key.Space, Source = searchBox };
            searchBox.RaiseEvent(spaceDown);
            Pump(0);
            Console.WriteLine($"[space-keydown] handled={spaceDown.Handled} open={combo.IsDropDownOpen} focused={searchBox.IsFocused} text=\"{searchBox.Text}\"");
            Check(!spaceDown.Handled, $"space KeyDown stays unhandled so the real window delivers TextInput (handled={spaceDown.Handled})");
            Check(combo.IsDropDownOpen, "dropdown stays open after an unhandled space KeyDown");

            // ---- B. Typing "song 1" (with a space) filters while keeping focus/selection. ----
            var filterCosts = new List<double>();
            foreach (var c in "song 1")
            {
                var charWatch = Stopwatch.StartNew();
                TypeChar(c);
                charWatch.Stop();
                filterCosts.Add(charWatch.Elapsed.TotalMilliseconds);
                Console.WriteLine($"[char '{c}'] text=\"{searchBox.Text}\" open={combo.IsDropDownOpen} focused={searchBox.IsFocused} fm={FocusedDesc()} source={SourceCount(combo)} realized={RealizedCount(combo)}");
            }
            Pump(0);

            var typedText = searchBox.Text ?? "";
            var filteredCount = SourceCount(combo);
            var expectedMatches = items.Count(x => x.DisplayName.Contains("song 1", StringComparison.OrdinalIgnoreCase));
            var expectedPinned = expectedMatches
                + (selectedOriginal != null && !selectedOriginal.DisplayName.Contains("song 1", StringComparison.OrdinalIgnoreCase) ? 1 : 0);
            Console.WriteLine($"[typed] text=\"{typedText}\" open={combo.IsDropDownOpen} filtered={filteredCount} expected={expectedPinned} realized={RealizedCount(combo)} perChar=[{string.Join(",", filterCosts.Select(v => v.ToString("F1")))}]ms");
            Check(typedText == "song 1", $"space characters are inserted into the search box (text=\"{typedText}\")");
            Check(combo.IsDropDownOpen, "dropdown stays open while typing spaces");
            Check(searchBox.IsFocused, $"focus stays in the search box while filtering (focused={FocusedDesc()})");
            Check(filteredCount == 101 && expectedPinned == 101,
                $"filtered list is the 100 matches plus the pinned selection (filtered={filteredCount}, expected={expectedPinned})");
            Check(combo.SelectedItem is SongItem keptSelected && ReferenceEquals(keptSelected, selectedOriginal),
                $"selection stays on the original item while filtering (selected={(combo.SelectedItem as SongItem)?.DisplayName ?? "null"})");
            Check(RealizedCount(combo) < 100, $"filtered dropdown remains virtualized (realized={RealizedCount(combo)})");
            Check(selectionEvents.All(x => x.Contains("Song 010") || x.Contains("null")),
                $"selection is only transiently cleared while filtering (events=[{string.Join(" | ", selectionEvents)}])");
            Check(filterCosts.All(v => v < 1000), $"each keystroke stays responsive (max={filterCosts.Max():F1}ms)");

            // ---- C. A no-match query keeps only the pinned selected item. ----
            searchBox.Text = "";
            Pump(0);
            TypeText("zzzzzz");
            var pinnedCount = SourceCount(combo);
            Console.WriteLine($"[nomatch] text=\"{searchBox.Text}\" filtered={pinnedCount} selected={(combo.SelectedItem as SongItem)?.DisplayName ?? "null"} realized={RealizedCount(combo)}");
            Check(pinnedCount == 1, $"no-match query keeps only the pinned selected item (filtered={pinnedCount})");
            Check(combo.SelectedItem is SongItem pinned && ReferenceEquals(pinned, selectedOriginal),
                "selected item survives a no-match filter");

            // ---- D. Clearing the query restores the original list; closing resets search state. ----
            searchBox.Text = "";
            Pump(0);
            var restoredCount = SourceCount(combo);
            Console.WriteLine($"[restore] text=\"{searchBox.Text}\" source={restoredCount} selected={(combo.SelectedItem as SongItem)?.DisplayName ?? "null"}");
            Check(restoredCount == 736, $"clearing the query restores all 736 items (source={restoredCount})");
            Check(ReferenceEquals(combo.ItemsSource, items), "restored ItemsSource is the original instance");
            Check(combo.SelectedItem is SongItem restoredSelected && ReferenceEquals(restoredSelected, selectedOriginal),
                "selection is preserved after clearing the query");

            searchBox.Text = "song 1";
            Pump(0);
            var filteredBeforeClose = SourceCount(combo);
            combo.IsDropDownOpen = false;
            Pump(5);
            var afterCloseCount = SourceCount(combo);
            var afterCloseText = searchBox.Text ?? "";
            Console.WriteLine($"[close] filteredBeforeClose={filteredBeforeClose} open={combo.IsDropDownOpen} source={afterCloseCount} searchText=\"{afterCloseText}\" selected={(combo.SelectedItem as SongItem)?.DisplayName ?? "null"}");
            Check(!combo.IsDropDownOpen, "dropdown is closed");
            Check(afterCloseCount == 736, $"closing restores all 736 items (source={afterCloseCount})");
            Check(ReferenceEquals(combo.ItemsSource, items), "closing restores the original ItemsSource instance");
            Check(afterCloseText == "", "closing clears the search text");
            Check(combo.SelectedItem is SongItem closingSelected && ReferenceEquals(closingSelected, selectedOriginal),
                "selection survives closing the dropdown");
        }
        catch (Exception exception)
        {
            Console.WriteLine("[FAIL] unhandled exception: " + exception);
            Environment.Exit(1);
        }

        Console.WriteLine(_failures == 0 ? "[RESULT] ALL CHECKS PASSED" : $"[RESULT] {_failures} CHECK(S) FAILED");
        Environment.Exit(_failures == 0 ? 0 : 1);
        return _failures == 0 ? 0 : 1;
    }
}
