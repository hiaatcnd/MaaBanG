using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Collections;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Controls.Primitives;
using Avalonia.Controls.Templates;
using Avalonia.Data;
using Avalonia.Layout;
using Avalonia.Input;
using Avalonia.Markup.Xaml.Styling;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using MFAAvalonia.Extensions.MaaFW;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Helper;

// The service owns credentials. Only the native view has its private pipe/token;
// neither task parameters, task settings nor logs contain login information.
public sealed class MaaBanGBestdoriClient
{
    private Process? _process;
    private HttpClient? _http;
    private readonly SemaphoreSlim _operations = new(1, 1);
    public async Task StartAsync(string? python = null, string? script = null)
    {
        var start = new ProcessStartInfo
        {
            FileName = python ?? Path.Combine(AppPaths.DataRoot, "python", "python.exe"),
            WorkingDirectory = AppPaths.DataRoot,
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
        };
        start.ArgumentList.Add("-u");
        start.ArgumentList.Add(script ?? Path.Combine(AppPaths.AgentDirectory, "bestdori_desktop.py"));
        start.Environment["MAABANG_DATA_DIR"] = AppPaths.UserDataRoot;
        start.Environment["PYTHONUTF8"] = "1";
        _process = Process.Start(start) ?? throw new IOException("无法启动档案工具");
        _ = DrainAsync(_process.StandardError);
        try
        {
            var line = await _process.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(15));
            var ready = JObject.Parse(line ?? throw new IOException("档案工具未能启动，请检查安装包是否完整"));
            var uri = new Uri(ready.Value<string>("url")!);
            if (uri.Scheme != "http" || uri.Host != "127.0.0.1" || uri.Fragment.Length < 32)
                throw new IOException("档案工具连接无效");
            _http = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
            { BaseAddress = new Uri(uri.GetLeftPart(UriPartial.Authority)), Timeout = TimeSpan.FromSeconds(90) };
            _http.DefaultRequestHeaders.Add("X-MaaBanG-Token", uri.Fragment[1..]);
            _ = DrainAsync(_process.StandardOutput);
        }
        catch { Disconnect(); throw; }
    }

    private static async Task DrainAsync(StreamReader stream)
    {
        try { while (await stream.ReadLineAsync() != null) { } } catch (IOException) { }
    }

    public async Task<JObject> CallAsync(string action, JObject? body = null)
    {
        var http = _http ?? throw new IOException("档案工具尚未连接");
        // Autosave can already be in flight when a user starts an operation.
        // Match the service's mutation lock, without delaying scan cancellation,
        // scan completion, public card media or read-only state polling.
        var serialize = body != null && action is not ("card" or "cancel" or "scan_finish");
        if (serialize) await _operations.WaitAsync();
        try
        {
            using var response = body == null ? await http.GetAsync("/api/" + action) :
                await http.PostAsync("/api/" + action, new StringContent(body.ToString(Formatting.None), Encoding.UTF8, "application/json"));
            var value = JObject.Parse(await response.Content.ReadAsStringAsync());
            if (!response.IsSuccessStatusCode)
                throw new InvalidOperationException(value.Value<string>("error") ?? "操作未完成");
            return value;
        }
        finally { if (serialize) _operations.Release(); }
    }

    public void Disconnect()
    {
        // Closing the pipe cancels this service's scan and lets an in-flight
        // cloud append finish its readback. Do not terminate a save process.
        try { _process?.StandardInput.Close(); } catch (InvalidOperationException) { }
        _process?.Dispose(); _process = null;
        _http?.Dispose(); _http = null;
    }
}

public sealed class MaaBanGBestdoriRow : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;
    private string _id = "", _identity = "", _level = "", _skill = "", _master = "", _train = "";
    private string _warning = "", _skillText = "", _mediaError = "";
    private string _artworkCaption = "卡面";
    private string _skillName = "";
    private string[] _skillDescriptions = Array.Empty<string>();
    private Bitmap? _artwork, _normalArtwork;
    private bool _selected = true;
    private JObject? _baseValues;
    private bool _takeMax, _inScope = true, _duplicateBase;
    private int _levelLimit = 60, _trainingLevel;
    public bool NeedsReview { get; set; }
    public bool MaxLevel { get; private set; }
    public bool MaxSkill { get; private set; }
    public bool MaxMaster { get; private set; }
    public string LevelDisplay { get => Empty(Level); set { MaxLevel = false; Level = Raw(value); } }
    public string SkillDisplay { get => Empty(Skill); set { MaxSkill = false; Skill = Raw(value); } }
    public string MasterDisplay { get => Empty(Master); set { MaxMaster = false; Master = Raw(value); } }
    public string TrainDisplay { get => Empty(Train); set { MaxLevel = false; Train = Raw(value); } }
    public string LevelEdit { get => Level; set => LevelDisplay = value; }
    public string SkillEdit { get => Skill; set => SkillDisplay = value; }
    public string MasterEdit { get => Master; set => MasterDisplay = value; }
    public string TrainEdit { get => Train; set => TrainDisplay = value; }
    public JObject OriginalValues { get; private set; } = new();
    public void RememberOriginal(JObject value) { OriginalValues = (JObject)value.DeepClone(); NotifyComparison(); }
    public void SetComparison(JObject? baseline, bool takeMax, bool inScope, bool duplicate, int levelLimit, int trainingLevel)
    {
        _baseValues = baseline; _takeMax = takeMax; _inScope = inScope; _duplicateBase = duplicate;
        _levelLimit = levelLimit; _trainingLevel = trainingLevel; NotifyComparison(); RefreshSkillText();
    }
    private int? Goal(string field)
    {
        var before = _baseValues?.Value<int?>(field);
        if (!Selected || !_inScope) return before;
        var raw = field == "level" ? Level : field == "skill" ? Skill : field == "master" ? Master : Train;
        if (!int.TryParse(raw, out var current)) return before;
        if (field == "skill") current--;
        return _takeMax && before.HasValue ? Math.Max(before.Value, current) : current;
    }
    private bool CanMerge
    {
        get
        {
            if (!Selected || !_inScope || _duplicateBase || !int.TryParse(Id, out _)) return false;
            foreach (var (raw, min, max) in new[] { (Level,1,60), (Skill,1,5), (Master,0,4), (Train,0,1) })
                if (raw.Length > 0 && (!int.TryParse(raw,out var number) || number < min || number > max)) return false;
            return new[] { "level", "skill", "master", "train" }.All(f => Goal(f).HasValue)
                && Goal("level") <= _levelLimit + (Goal("train") == 1 ? _trainingLevel : 0);
        }
    }
    public bool HasChanges => CanMerge && (_baseValues == null || new[] { "level", "skill", "master", "train" }.Any(f => Goal(f) != _baseValues.Value<int>(f)));
    private static string NumberText(int? value, int offset = 0) => value.HasValue ? (value.Value + offset).ToString() : "—";
    private string BaseText(string field, int offset = 0) => _duplicateBase ? "重复 ID" : NumberText(_baseValues?.Value<int?>(field), offset);
    private string ReadText(string field, int offset = 0) => NumberText(OriginalValues.Value<int?>(field), offset);
    private string GoalText(string field, int offset = 0) => NumberText(Goal(field), offset);
    private IBrush GoalBrush(string field)
    {
        if (!HasChanges || Goal(field) == _baseValues?.Value<int?>(field)) return new SolidColorBrush(Color.Parse("#475569"));
        return new SolidColorBrush(Color.Parse(Goal(field) < _baseValues?.Value<int?>(field) ? "#92400E" : "#166534"));
    }
    public string BaseLevelText => BaseText("level"); public string ReadLevelText => ReadText("level"); public string GoalLevelText => GoalText("level"); public IBrush GoalLevelBrush => GoalBrush("level");
    public string BaseSkillText => BaseText("skill",1); public string ReadSkillText => ReadText("skill",1); public string GoalSkillText => GoalText("skill",1); public IBrush GoalSkillBrush => GoalBrush("skill");
    public string BaseMasterText => BaseText("master"); public string ReadMasterText => ReadText("master"); public string GoalMasterText => GoalText("master"); public IBrush GoalMasterBrush => GoalBrush("master");
    public string BaseTrainText => BaseText("train"); public string ReadTrainText => ReadText("train"); public string GoalTrainText => GoalText("train"); public IBrush GoalTrainBrush => GoalBrush("train");
    private void NotifyComparison()
    {
        foreach (var field in new[] { "Level", "Skill", "Master", "Train" })
            foreach (var name in new[] { "Base" + field + "Text", "Read" + field + "Text", "Goal" + field + "Text", "Goal" + field + "Brush" })
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasChanges)));
    }
    public void RestoreOriginal()
    {
        LevelDisplay = OriginalValues["level"]?.ToString() ?? "";
        SkillDisplay = OriginalValues.Value<int?>("skill") is { } skill ? (skill + 1).ToString() : "";
        MasterDisplay = OriginalValues["master"]?.ToString() ?? "";
        TrainDisplay = OriginalValues["train"]?.ToString() ?? "";
    }
    public bool RaiseTo(string field, int target, int upper, int untrainedLevel)
    {
        var current = field == "level" ? Level : field == "skill" ? Skill : Master;
        var original = OriginalValues.Value<int?>(field);
        if (field == "skill" && original.HasValue) original++;
        if (!int.TryParse(current, out var value) && !original.HasValue) return false;
        value = Math.Max(value, original ?? 0);
        var raised = Math.Max(value, Math.Min(target, upper));
        if (field == "level")
        {
            LevelDisplay = raised.ToString();
            if (raised > untrainedLevel) TrainDisplay = "1";
            else if (OriginalValues.Value<int?>("train") is { } train && train > (int.TryParse(Train, out var currentTrain) ? currentTrain : -1))
                TrainDisplay = train.ToString();
        }
        else if (field == "skill") SkillDisplay = raised.ToString();
        else MasterDisplay = raised.ToString();
        return true;
    }
    private static string Empty(string value) => string.IsNullOrEmpty(value) ? "未读取" : value;
    private static string Raw(string value) => value is "未读取" or "按上限" ? "" : value;
    public void SetMaximum(bool level, bool skill, bool master, int levelLimit, bool train)
    {
        if (level) { Level = levelLimit.ToString(); Train = train ? "1" : "0"; MaxLevel = true; }
        if (skill) { Skill = "5"; MaxSkill = true; }
        if (master) { Master = "4"; MaxMaster = true; }
        foreach (var name in new[] { nameof(LevelDisplay), nameof(SkillDisplay), nameof(MasterDisplay), nameof(TrainDisplay) }) Changed(name);
    }
    public string Identity { get => _identity; set { _identity = value; Changed(nameof(Identity)); } }
    public string Warning { get => _warning; set { _warning = value; Changed(nameof(Warning)); } }
    public string SkillText { get => _skillText; set { _skillText = value; Changed(nameof(SkillText)); } }
    public string MediaError { get => _mediaError; set { _mediaError = value; Changed(nameof(MediaError)); RefreshSkillText(); } }
    public Bitmap? Artwork { get => _artwork; set { _artwork = value; Changed(nameof(Artwork)); } }
    public Bitmap? NormalArtwork { get => _normalArtwork; set { _normalArtwork = value; Changed(nameof(NormalArtwork)); Changed(nameof(HasNormalArtwork)); } }
    public bool HasNormalArtwork => NormalArtwork != null;
    public string ArtworkCaption { get => _artworkCaption; set { _artworkCaption=value; Changed(nameof(ArtworkCaption)); } }
    public bool Selected { get => _selected; set { _selected = value; Changed(nameof(Selected)); } }
    public string Id { get => _id; set { _id = value; Changed(nameof(Id)); } }
    public string Level { get => _level; set { _level = value; Changed(nameof(Level)); } }
    public string Skill { get => _skill; set { _skill = value; Changed(nameof(Skill)); RefreshSkillText(); } }
    public string Master { get => _master; set { _master = value; Changed(nameof(Master)); } }
    public string Train { get => _train; set { _train = value; Changed(nameof(Train)); } }
    public void SetSkillDetails(string name, string[] descriptions)
    {
        _skillName = name; _skillDescriptions = descriptions; RefreshSkillText();
    }
    private void RefreshSkillText()
    {
        if (_skillDescriptions.Length == 0) return;
        var level = Goal("skill");
        if (!level.HasValue && int.TryParse(Skill,out var observedLevel) && observedLevel is >= 1 and <= 5)
            level = observedLevel - 1;
        var effect = level is >= 0 and <= 4
            ? _skillDescriptions.ElementAtOrDefault(level.Value) : "技能等级未读取，确认后显示对应效果";
        SkillText = _skillName + "\n" + (string.IsNullOrWhiteSpace(effect) ? "技能效果暂不可用，请重试加载资料" : effect)
            + (MediaError.Length == 0 ? "" : "\n" + MediaError);
    }
    private void Changed(string name)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        if (name is nameof(Level) or nameof(Skill) or nameof(Master) or nameof(Train))
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name+"Display"));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name+"Edit"));
        }
        if (name is nameof(Level) or nameof(Skill) or nameof(Master) or nameof(Train) or nameof(Selected))
        { NotifyComparison(); RefreshSkillText(); }
    }

    public JObject Observation(bool maxLevel = false, bool maxSkill = false, bool maxMaster = false)
    {
        static int Number(string text, string label)
        {
            if (!int.TryParse(text, out var n)) throw new InvalidOperationException(label + "需要填写整数");
            return n;
        }
        if (!int.TryParse(Id, out var id) || id <= 0)
            throw new InvalidOperationException("有勾选的卡牌身份尚未确认，请通过搜索确认身份，或取消勾选该记录。");
        var value = new JObject { ["id"] = id, ["maximize"] = new JObject { ["level"] = MaxLevel || maxLevel, ["skill"] = MaxSkill || maxSkill, ["master"] = MaxMaster || maxMaster } };
        void Add(string key, string text, bool forced, int offset = 0)
        {
            if (!forced && !string.IsNullOrWhiteSpace(text)) value[key] = Number(text, key) + offset;
        }
        Add("level", Level, maxLevel); Add("train", Train, maxLevel);
        Add("skill", Skill, maxSkill, -1); Add("master", Master, maxMaster);
        return value;
    }
}

// Keep reference readings visible while editing only the target line.
public sealed class MaaBanGBestdoriNumberColumn : DataGridTemplateColumn
{
    public string DisplayProperty { get; set; } = "";
    public Action<string>? ReportError { get; set; }
    public MaaBanGBestdoriNumberColumn()
    { CellTemplate = new FuncDataTemplate<MaaBanGBestdoriRow>((row, _) => CreateCell(row!)); }
    private Control CreateCell(MaaBanGBestdoriRow row)
    {
        var field = DisplayProperty.Replace("Display", "");
        var panel = new Grid { RowDefinitions = new RowDefinitions("22,22,28"), VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(6) };
        foreach (var (prefix, index) in new[] { ("Base",0), ("Read",1) })
        {
            var text = new TextBlock { FontSize = 14, Width = 64, TextAlignment = TextAlignment.Center, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Center, Foreground = new SolidColorBrush(Color.Parse("#64748B")) };
            text.Bind(TextBlock.TextProperty, new Binding(prefix + field + "Text"));
            Grid.SetRow(text,index); panel.Children.Add(text);
        }
        var editor = new TextBox { Name = "BestdoriTarget" + field, Width = 64, Height = 28, MinHeight = 0,
            FontSize = 14, FontWeight = FontWeight.SemiBold, TextAlignment = TextAlignment.Center, Padding = new Thickness(6,2), CornerRadius = new CornerRadius(4),
            HorizontalAlignment = HorizontalAlignment.Left, VerticalContentAlignment = VerticalAlignment.Center,
            MaxLength = 2 };
        var property = typeof(MaaBanGBestdoriRow).GetProperty(field + "Edit")!;
        var goal = typeof(MaaBanGBestdoriRow).GetProperty("Goal" + field + "Text")!;
        var editing = false;
        void ShowGoal() => editor.Text = (string?)goal.GetValue(row);
        ShowGoal(); editor.Bind(TextBox.ForegroundProperty,new Binding("Goal" + field + "Brush"));
        row.PropertyChanged += (_, e) => { if (!editing && e.PropertyName == "Goal" + field + "Text") ShowGoal(); };
        bool Commit()
        {
            var value = (editor.Text ?? "").Trim();
            var min = field is "Level" or "Skill" ? 1 : 0;
            var max = field == "Level" ? 60 : field == "Skill" ? 5 : field == "Master" ? 4 : 1;
            if (value.Length > 0 && (!int.TryParse(value,out var n) || n < min || n > max))
            { ReportError?.Invoke($"{Header}请输入 {min}–{max} 的整数；本次输入未采用。"); return false; }
            property.SetValue(row,value); return true;
        }
        editor.GotFocus += (_, _) =>
        {
            editing = true;
            editor.Text = (string?)property.GetValue(row);
            if (string.IsNullOrEmpty(editor.Text)) { var text = (string?)goal.GetValue(row); editor.Text = text == "—" ? "" : text; }
            editor.SelectAll();
        };
        editor.LostFocus += (_, _) => { if (editing) Commit(); editing = false; ShowGoal(); };
        editor.KeyDown += (_, e) =>
        {
            if (e.Key != Key.Enter && e.Key != Key.Escape) return;
            e.Handled = true;
            if (e.Key == Key.Enter && !Commit()) return;
            editing = false; ShowGoal(); TopLevel.GetTopLevel(editor)?.FocusManager?.ClearFocus();
        };
        Grid.SetRow(editor,2); panel.Children.Add(editor);
        return panel;
    }
}

// A partial band selects all its members on the next click.
public sealed class MaaBanGBestdoriBandCheckBox : CheckBox
{
    protected override Type StyleKeyOverride => typeof(CheckBox);
    protected override void Toggle() => SetCurrentValue(IsCheckedProperty, IsChecked != true);
}

/// <summary>An independent tool page; only ReadGame reserves the task runner.</summary>
public sealed class MaaBanGBestdoriView : UserControl
{
    private MaaBanGBestdoriClient? _client;
    private bool _starting, _operation, _scanning, _polling, _loggedIn, _updatingScope;
    private int _revision;
    private string? _previewToken;
    private JObject _catalog = new();
    private CancellationTokenSource? _scanCancellation;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer _cacheTimer = new() { Interval = TimeSpan.FromMilliseconds(800) };
    private bool _restoring, _cacheDirty;
    private readonly List<Button> _actions = new();
    private readonly Dictionary<int, CheckBox> _bands = new(), _members = new(), _rarities = new();
    private static readonly string[] AttributeKinds = { "powerful", "cool", "pure", "happy" };
    private readonly Dictionary<string, CheckBox> _attributes = new();
    private readonly ObservableCollection<MaaBanGBestdoriRow> _rows = new();
    private readonly DataGridCollectionView _rowView;
    private JArray _baseCards = new();
    private readonly CheckBox _onlyChanged = new() { Content = "只看有变化" };
    private readonly CheckBox _skipExisting = new() { Content = "跳过已存在，只读取新卡" };
    private readonly TextBlock _comparisonSummary = Text("");
    private readonly DispatcherTimer _comparisonTimer = new() { Interval = TimeSpan.FromMilliseconds(200) };
    private readonly SemaphoreSlim _mediaSlots = new(3);
    private readonly TextBlock _status = Text("正在准备档案工具……"), _base = Text("空白中国服档案"),
        _account = Text("未登录"), _scanSummary = Text("尚未读取游戏"), _previewSummary = Text("生成预览后，确认修改内容再导出或保存到云端。");
    private readonly TextBlock _accountBadgeText = Text("Bestdori · 未登录"),
        _scopeSummary = Text(""), _scanScope = Text("");
    private readonly Border _accountBadge, _statusBanner;
    private readonly StackPanel _loginForm;
    private readonly TextBox _name = new() { Text = "MaaBanG", Watermark = "保存后的档案名称" },
        _importText = new() { AcceptsReturn = true, MinHeight = 90, Watermark = "粘贴 Bestdori 导出的资料" },
        _username = new() { Watermark = "Bestdori 用户名" }, _password = new() { Watermark = "密码", PasswordChar = '●' },
        _search = new() { Watermark = "搜索卡牌 ID、成员或卡面名称" };
    private readonly CheckBox _maxLevel = new() { Content = "等级" },
        _maxSkill = new() { Content = "技能等级" }, _maxMaster = new() { Content = "星光等级" };
    private readonly NumericUpDown _targetLevel = TargetNumber(1, 60, 60),
        _targetSkill = TargetNumber(1, 5, 5), _targetMaster = TargetNumber(0, 4, 4);
    private readonly ComboBox _policy = new() { ItemsSource = new[] { "各项取最大值", "按当前值覆盖" }, SelectedIndex = 0 },
        _cloud = new() { HorizontalAlignment = HorizontalAlignment.Stretch },
        _cardChoices = new() { HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly StackPanel _scope = new() { Spacing = 12 }, _preview = new() { Spacing = 8 };
    private readonly TabControl _tabs = new();
    private readonly DataGrid _table;
    private readonly Button _export, _copy, _upload, _replace, _stop, _logout, _cloudRead, _useCloud;
    private readonly TextBlock _replaceTarget = Text("");
    private JObject? _cloudTarget;
    private readonly MaaBanGBestdoriNumberColumn _levelColumn, _skillColumn, _masterColumn, _trainColumn;
    private JArray _cloudProfiles = new();

    public MaaBanGBestdoriView()
    {
        Styles.Add(new StyleInclude(new Uri("avares://MFAAvalonia.Core/"))
        { Source = new Uri("avares://Avalonia.Controls.DataGrid/Themes/Fluent.xaml") });
        _accountBadge = new Border { Child = _accountBadgeText, Padding = new Thickness(12, 7), CornerRadius = new CornerRadius(6), HorizontalAlignment = HorizontalAlignment.Left };
        _accountBadgeText.FontWeight = FontWeight.SemiBold;
        _account.FontSize = 18; _account.FontWeight = FontWeight.SemiBold;
        _statusBanner = new Border { Child = _status, Padding = new Thickness(12, 8), CornerRadius = new CornerRadius(6) };
        SetStatus("正在准备档案工具……");
        _rowView = new DataGridCollectionView(_rows);
        _rowView.Filter = item => _onlyChanged.IsChecked != true || ((MaaBanGBestdoriRow)item).HasChanges;
        _table = new DataGrid { ItemsSource = _rowView, AutoGenerateColumns = false, MinHeight = 180, MaxHeight = 560,
            MaxWidth = 1280, HorizontalAlignment = HorizontalAlignment.Left, FontSize = 14, CanUserResizeColumns = true, GridLinesVisibility = DataGridGridLinesVisibility.Horizontal };
        _table.Styles.Add(new Style(x => x.OfType<DataGridColumnHeader>())
        { Setters = { new Setter(TemplatedControl.PaddingProperty, new Thickness(6, 5)), new Setter(TemplatedControl.FontSizeProperty, 14d) } });
        _table.Columns.Add(new DataGridCheckBoxColumn { Header = new TextBlock { Text = "采用", FontSize = 14 }, Binding = Bind("Selected"), Width = new DataGridLength(78), MinWidth = 78 });
        _table.Columns.Add(new DataGridTemplateColumn { Header = "卡牌 · 技能", Width = new DataGridLength(490), MinWidth = 420, MaxWidth = 560,
            CellTemplate = new FuncDataTemplate<MaaBanGBestdoriRow>((row, _) =>
            {
                var layout = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*"), Margin = new Thickness(4,6) };
                var art = new Image { Width = 62, Height = 62, Stretch = Stretch.Uniform, VerticalAlignment = VerticalAlignment.Top };
                art.Bind(Image.SourceProperty, new Binding(nameof(row.Artwork)));
                var normal = new Image { Width = 62, Height = 62, Stretch = Stretch.Uniform };
                normal.Bind(Image.SourceProperty,new Binding(nameof(row.NormalArtwork)));
                var normalLabel=Text("特训前"); normalLabel.FontSize=11; normalLabel.HorizontalAlignment=HorizontalAlignment.Center;
                var normalPanel=new StackPanel { Spacing=2, Children={normal,normalLabel} };
                normalPanel.Bind(IsVisibleProperty,new Binding(nameof(row.HasNormalArtwork)));
                var artLabel = Text(""); artLabel.FontSize = 11; artLabel.HorizontalAlignment = HorizontalAlignment.Center;
                artLabel.Bind(TextBlock.TextProperty,new Binding(nameof(row.ArtworkCaption)));
                art.Bind(ToolTip.TipProperty,new Binding(nameof(row.Id)) { StringFormat="ID {0}" });
                var artPanel = new StackPanel { Width=129, VerticalAlignment=VerticalAlignment.Top, Orientation=Orientation.Horizontal, Spacing=5, Children={normalPanel,new StackPanel {Spacing=2,Children={art,artLabel}}} };
                layout.Children.Add(artPanel);
                var identity = Text(""); identity.FontWeight = FontWeight.SemiBold; identity.FontSize = 14;
                identity.Bind(TextBlock.TextProperty, new Binding(nameof(row.Identity)));
                var skill = Text(""); skill.FontSize = 12;
                skill.Bind(TextBlock.TextProperty, new Binding(nameof(row.SkillText)));
                skill.Bind(ToolTip.TipProperty, new Binding(nameof(row.SkillText)));
                var detail = new StackPanel { Spacing = 7, Margin = new Thickness(10,0,8,0), Children = { identity, skill } };
                Grid.SetColumn(detail, 1); layout.Children.Add(detail);
                ToolTip.SetTip(layout, "技能效果随右侧技能等级更新；修改数值只影响档案。");
                return layout;
            }) });
        _table.Columns.Add(new DataGridTemplateColumn { Header = "对照", Width = new DataGridLength(78), MinWidth = 78,
            CellTemplate = new FuncDataTemplate<MaaBanGBestdoriRow>((row, _) =>
            {
                var labels = new Grid { RowDefinitions = new RowDefinitions("22,22,28"), VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(4,6) };
                foreach (var (label,index) in new[] { ("底稿",0), ("读取",1), ("目标",2) })
                {
                    var text = Text(label); text.FontSize = 12; text.VerticalAlignment = VerticalAlignment.Center;
                    text.Foreground = new SolidColorBrush(Color.Parse(index == 2 ? "#334155" : "#64748B"));
                    Grid.SetRow(text,index); labels.Children.Add(text);
                }
                return labels;
            }) });
        _table.Columns.Add(_levelColumn = Column("等级", "LevelDisplay", 92));
        _table.Columns.Add(_skillColumn = Column("技能等级", "SkillDisplay", 100));
        _table.Columns.Add(_masterColumn = Column("星光等级", "MasterDisplay", 100));
        _table.Columns.Add(_trainColumn = Column("特训", "TrainDisplay", 92));
        _table.Columns.Add(new DataGridTemplateColumn { Header = "核对提示", Width = new DataGridLength(150), MinWidth = 150,
            CellTemplate = new FuncDataTemplate<MaaBanGBestdoriRow>((row, _) =>
            {
                var text = Text(""); text.FontSize = 12; text.Margin = new Thickness(8); text.VerticalAlignment = VerticalAlignment.Center;
                text.Bind(TextBlock.TextProperty, new Binding(nameof(row.Warning))); return text;
            }) });

        _export = Button("导出文件", ExportAsync); _copy = Button("复制资料", CopyAsync);
        _upload = Button("新增云端档案", UploadAsync);
        _replace = Button("替换当前云端底稿", ReplaceAsync);
        _stop = new Button { Content = "停止读取", IsEnabled = false };
        _stop.Click += async (_, _) => { _stop.IsEnabled = false; try { await Api("cancel"); }
            catch (Exception ex) { SetStatus(ex.Message, true); } finally { _scanCancellation?.Cancel(); } };

        var settings = Stack(
            Heading("1  选择底稿"), _base,
            Wrap(Button("导入资料文件", ImportFileAsync), Button("从空白档案开始", async () => { await Api("empty"); Dirty(); await RefreshAsync(); })),
            new Expander { Header = "或粘贴资料", Content = Stack(_importText, Button("使用粘贴资料", async () => await ImportAsync(_importText.Text ?? ""))) },
            Text("也可在“Bestdori 账号”中选择云端底稿。底稿中的其他卡牌、道具和角色潜能会保留。"),
            Heading("2  选择更新范围"), Text("勾选乐队会选中整队成员；也可单独选择成员。乐队的横杠表示只选了部分成员。"), _scope, _scopeSummary,
            Heading("3  设置更新规则"), Text("同一卡牌发生冲突时"), _policy,
            Text("保存后的档案名称"), _name,
            Button("下一步：读取与核对", () => { _tabs.SelectedIndex = 1; return Task.CompletedTask; }));

        var scanButton = Button("读取游戏", ScanAsync);
        ToolTip.SetTip(scanButton,"使用主页当前设备；游戏停在主页、乐队菜单或成员列表。只读取卡牌，不消耗材料。");
        var review = Stack(_scanScope, _skipExisting,
            Text("开启后按当前底稿跳过已有卡，保留其培养数值。小图无法确认时进入详情核对；首次使用会准备卡牌小图。"),
            Wrap(scanButton, _stop, _scanSummary),
            Wrap(Wrap(_maxLevel, _targetLevel), Wrap(_maxSkill, _targetSkill), Wrap(_maxMaster, _targetMaster),
                Button("提升勾选卡牌", ApplyTargetsAsync), Button("还原勾选卡牌", RestoreOriginalAsync)),
            Text("按读取原值、当前值和指定值逐项取最大值，指定值不超过卡牌上限。还原会恢复读取原值。仅修改档案，不培养游戏卡牌。"),
            Wrap(_onlyChanged, _comparisonSummary, Text("点击目标数值修改，回车确认，Esc 取消；目标按冲突规则计算，绿色提升、橙色降低。")),
            _table,
            Wrap(Button("全选已确认卡牌", () => { foreach (var row in _rows) row.Selected = !row.NeedsReview && int.TryParse(row.Id, out _); return Task.CompletedTask; }),
                Button("全不选", () => { foreach (var row in _rows) row.Selected = false; return Task.CompletedTask; }),
                Button("重试加载小图与技能", async () => { await Task.WhenAll(_rows.Where(r => r.Artwork == null || r.MediaError.Length > 0).Select(LoadMediaAsync)); })),
            new Expander { Header = "搜索卡牌 / 确认身份", Content = Stack(_search, _cardChoices,
                Wrap(Button("确认当前记录身份", () =>
                {
                    if (_table.SelectedItem is not MaaBanGBestdoriRow row || _cardChoices.SelectedItem is not CardChoice choice)
                        throw new InvalidOperationException("请先选中一条待确认记录，再搜索并选择正确卡牌。");
                    row.Id = choice.Id.ToString(); row.NeedsReview = false; row.Warning = "已由你手动确认身份"; row.Selected = true; return Task.CompletedTask;
                }), Button("添加所选卡牌", () => { if (_cardChoices.SelectedItem is CardChoice choice) AddRow(new JObject { ["id"] = choice.Id }); return Task.CompletedTask; }))) },
            Button("查看合并结果", PreviewAsync));
        var result = Stack(Heading("合并预览"), _previewSummary,
            Wrap(Button("重新计算结果", PreviewAsync), _export, _copy),
            Heading("保存到 Bestdori"), _replaceTarget, Wrap(_replace, _upload),
            Text("替换会保存当前合并结果；新增需使用新名称。保存前自动备份，请避免同时在其他设备编辑云端档案。"), _preview);
        _loginForm = Stack(_username, _password, Button("登录", LoginAsync));
        _logout = Button("退出登录", async () =>
        { try { await Api("logout"); } finally { _cloudProfiles.Clear(); _cloud.ItemsSource = null; await RefreshAsync(); } });
        _cloudRead = Button("读取云端档案", CloudAsync);
        _useCloud = Button("使用所选底稿", async () =>
            {
                if (_cloud.SelectedIndex < 0) throw new InvalidOperationException("请先选择云端档案");
                var chosen = _cloudProfiles[_cloud.SelectedIndex];
                await Api("cloud_base", new JObject { ["index"] = chosen["index"]!.DeepClone(), ["key"] = chosen["key"]!.DeepClone() });
                _name.Text = chosen.Value<string>("name");
                Dirty(); await RefreshAsync(); _tabs.SelectedIndex = 0;
            });
        var account = Stack(Heading("Bestdori 账号"), _account, _loginForm, _logout,
            Text("登录仅在本次工具会话中有效，密码和登录凭据不保存。"), Heading("使用云端档案作为底稿"),
            _cloudRead, _cloud, _useCloud,
            Text("使用云端底稿后，可在“合并与导出”中直接替换该档案，也可改名另存。"));
        account.MaxWidth = 640; account.HorizontalAlignment = HorizontalAlignment.Left;
        settings.MaxWidth = 1120; settings.HorizontalAlignment = HorizontalAlignment.Left;
        _name.MaxWidth = _policy.MaxWidth = 420; _name.HorizontalAlignment = _policy.HorizontalAlignment = HorizontalAlignment.Left;
        _username.Width = _password.Width = 340;
        _cloud.Width = 460; _cloud.HorizontalAlignment = HorizontalAlignment.Left;
        foreach (var (title, body) in new[] { ("底稿与范围", settings), ("读取与核对", review), ("合并与导出", result), ("Bestdori 账号", account) })
            _tabs.Items.Add(new TabItem { Header = new TextBlock { Text = title, FontSize = 15 }, Content = new ScrollViewer { Content = body, HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled } });
        var layout = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto"), Margin = new Thickness(18) };
        var header = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Margin = new Thickness(0,0,0,4) };
        header.Children.Add(Heading("Bestdori 档案"));
        Grid.SetColumn(_accountBadge,1); _accountBadge.VerticalAlignment = VerticalAlignment.Center; header.Children.Add(_accountBadge);
        layout.Children.Add(Stack(header, _statusBanner));
        Grid.SetRow(_tabs, 1); layout.Children.Add(_tabs);
        var footer = Stack(Wrap(Button("重新连接工具", StartAsync), Button("更新卡牌目录", async () =>
        { var value = await Api("refresh"); _catalog = (JObject)value["catalog"]!; BuildScope(); Dirty(); })));
        Grid.SetRow(footer, 2); layout.Children.Add(footer); Content = layout;
        _name.TextChanged += (_, _) => Dirty();
        _policy.SelectionChanged += (_, _) => Dirty();
        _onlyChanged.IsCheckedChanged += (_, _) =>
        {
            _table.CommitEdit(DataGridEditingUnit.Cell,true); _table.CommitEdit(DataGridEditingUnit.Row,true);
            RefreshComparison();
        };
        _comparisonTimer.Tick += (_, _) =>
        {
            if (_rowView.IsEditingItem) return;
            _comparisonTimer.Stop(); RefreshComparison();
        };
        _table.CellEditEnded += (_, _) => Dispatcher.UIThread.Post(() =>
        { _table.CommitEdit(DataGridEditingUnit.Row,true); RefreshComparison(); });
        _cloud.SelectionChanged += (_, _) => UpdateButtons();
        foreach (var check in new[] { _maxLevel, _maxSkill, _maxMaster })
            check.IsCheckedChanged += (_, _) => UpdateButtons();
        _search.TextChanged += (_, _) => SearchCards();
        Loaded += async (_, _) => { if (_client == null) await Guard(StartAsync); };
        _timer.Tick += async (_, _) =>
        {
            if (_polling || _client == null || !_scanning) return;
            _polling = true;
            try { await RefreshAsync(); } catch (Exception ex) { SetStatus(ex.Message, true); }
            finally { _polling = false; }
        };
        _cacheTimer.Tick += async (_, _) =>
        {
            _cacheTimer.Stop();
            if (!_cacheDirty || _client == null || _restoring || _scanning || _operation) return;
            try { await SaveReviewAsync(); }
            catch { _cacheDirty = true; }
        };
        if (Application.Current?.ApplicationLifetime is IClassicDesktopStyleApplicationLifetime lifetime)
            lifetime.Exit += (_, _) => { _scanCancellation?.Cancel(); _client?.Disconnect(); };
        RenderAccount(null); RenderCloudTarget(null); UpdateButtons();
    }

    private static Binding Bind(string path) => new(path) { Mode = BindingMode.TwoWay };
    private MaaBanGBestdoriNumberColumn Column(string header, string property, int width) => new()
        { Header = header, DisplayProperty = property, ReportError = message => SetStatus(message,true), MinWidth = width, Width = new DataGridLength(width), IsReadOnly = true };
    private static NumericUpDown TargetNumber(int min, int max, int value) => new()
        { Minimum = min, Maximum = max, Value = value, Increment = 1, FormatString = "0", Width = 90 };
    private static TextBlock Text(string value) => new() { Text = value, TextWrapping = TextWrapping.Wrap };
    private static TextBlock Heading(string value) => new() { Text = value, FontSize = 19, FontWeight = FontWeight.SemiBold, Margin = new Thickness(0, 10, 0, 4) };
    private static StackPanel Stack(params Control[] controls)
    {
        var panel = new StackPanel { Spacing = 10, Margin = new Thickness(0, 4, 8, 12) };
        foreach (var control in controls) panel.Children.Add(control);
        return panel;
    }
    private static WrapPanel Wrap(params Control[] controls)
    {
        var panel = new WrapPanel();
        foreach (var control in controls) { control.Margin = new Thickness(0, 0, 10, 6); panel.Children.Add(control); }
        return panel;
    }
    private Button Button(string title, Func<Task> action)
    {
        var button = new Button { Content = title, HorizontalAlignment = HorizontalAlignment.Left, Padding = new Thickness(16,7) };
        if (title is "读取游戏" or "查看合并结果" or "新增云端档案" or "登录")
        { button.Background = new SolidColorBrush(Color.Parse("#3563E9")); button.Foreground = Brushes.White; button.FontWeight = FontWeight.SemiBold; }
        button.Click += async (_, _) => await Guard(action);
        _actions.Add(button); return button;
    }
    private async Task Guard(Func<Task> action)
    {
        if (_operation || _scanning) return;
        _operation = true; UpdateButtons();
        try { await action(); }
        catch (Exception ex) { SetStatus(ex is HttpRequestException or TaskCanceledException ? "连接未完成，请检查网络；若刚才保存到云端，请读取云端档案确认结果。" : ex.Message, true); }
        finally { _operation = false; UpdateButtons(); }
    }
    private void Dirty()
    {
        _revision++; _previewToken = null; _preview.Children.Clear();
        _comparisonTimer.Stop(); _comparisonTimer.Start();
        _previewSummary.Text = "设置或记录已更改，请重新生成合并预览。";
        UpdateButtons();
    }
    private void UpdateButtons()
    {
        foreach (var button in _actions) button.IsEnabled = !_operation && !_scanning;
        _export.IsEnabled = _copy.IsEnabled = !_operation && !_scanning && _previewToken != null;
        _upload.IsEnabled = _copy.IsEnabled && _loggedIn;
        _replace.IsEnabled = _upload.IsEnabled && _cloudTarget != null;
        _cloudRead.IsEnabled = !_operation && !_scanning && _loggedIn;
        _useCloud.IsEnabled = _cloudRead.IsEnabled && _cloud.SelectedIndex >= 0;
        _stop.IsEnabled = _scanning && _scanCancellation?.IsCancellationRequested != true;
        _tabs.IsEnabled = !_starting;
        _table.IsEnabled = !_scanning && !_operation;
        _onlyChanged.IsEnabled = !_scanning && !_operation;
        _skipExisting.IsEnabled = !_scanning && !_operation;
        _scope.IsEnabled = _name.IsEnabled = _policy.IsEnabled = !_scanning && !_operation;
        foreach (var check in new[] { _maxLevel, _maxSkill, _maxMaster }) check.IsEnabled = !_scanning && !_operation;
        _targetLevel.IsEnabled = _maxLevel.IsEnabled && _maxLevel.IsChecked == true;
        _targetSkill.IsEnabled = _maxSkill.IsEnabled && _maxSkill.IsChecked == true;
        _targetMaster.IsEnabled = _maxMaster.IsEnabled && _maxMaster.IsChecked == true;
    }
    private void ApplyMaximum(MaaBanGBestdoriRow row, JObject flags)
    {
        if (_catalog["cards"]?[row.Id] is not JObject card) return;
        var train = card.Value<int>("trainingLevel");
        row.SetMaximum(flags.Value<bool>("level"),flags.Value<bool>("skill"),flags.Value<bool>("master"),card.Value<int>("levelLimit")+train,train>0);
    }
    private JObject Targets()
    {
        var targets = new JObject();
        foreach (var (field, check, input) in new[] { ("level", _maxLevel, _targetLevel), ("skill", _maxSkill, _targetSkill), ("master", _maxMaster, _targetMaster) })
            if (check.IsChecked == true)
            {
                if (input.Value is not { } value || value != decimal.Truncate(value) || value < input.Minimum || value > input.Maximum)
                    throw new InvalidOperationException("请填写范围内的整数目标值。");
                targets[field] = (int)value;
            }
        return targets;
    }
    private int ApplyTargets(IEnumerable<MaaBanGBestdoriRow> rows, JObject targets)
    {
        var missing = 0;
        foreach (var row in rows)
        {
            if (_catalog["cards"]?[row.Id] is not JObject card) { missing++; continue; }
            foreach (var field in targets.Properties())
            {
                var limit = field.Name == "level" ? card.Value<int>("levelLimit") + card.Value<int>("trainingLevel") : field.Name == "skill" ? 5 : 4;
                if (!row.RaiseTo(field.Name, field.Value.Value<int>(), limit, card.Value<int>("levelLimit"))) missing++;
            }
        }
        return missing;
    }
    private async Task ApplyTargetsAsync()
    {
        if (!_table.CommitEdit(DataGridEditingUnit.Cell, true) || !_table.CommitEdit(DataGridEditingUnit.Row, true))
            throw new InvalidOperationException("请先完成表格中的修改。");
        var selected = _rows.Where(r => r.Selected).ToList();
        if (selected.Count == 0) throw new InvalidOperationException("请先在左侧勾选要提升的卡牌。");
        if (selected.Any(r => !int.TryParse(r.Id,out _))) throw new InvalidOperationException("请先确认所勾选卡牌的身份。");
        var targets = Targets();
        if (!targets.HasValues) throw new InvalidOperationException("请选择要提升的项目并填写目标数值。");
        var missing = ApplyTargets(selected, targets);
        Dirty(); await SaveReviewAsync(); SetStatus($"已处理 {selected.Count} 张勾选卡牌。" + (missing == 0 ? "数值只升不降，可一键还原读取值。" : $"{missing} 项没有已知数值，已保留为空，请读取或核对后再应用。"));
    }
    private async Task RestoreOriginalAsync()
    {
        _table.CancelEdit(DataGridEditingUnit.Cell); _table.CancelEdit(DataGridEditingUnit.Row);
        var selected = _rows.Where(r => r.Selected).ToList();
        if (selected.Count == 0) throw new InvalidOperationException("请先勾选要还原的卡牌。");
        foreach (var row in selected) row.RestoreOriginal();
        Dirty(); await SaveReviewAsync(); SetStatus($"已还原 {selected.Count} 张勾选卡牌的读取值；未读取的项目恢复为空。");
    }
    private async Task StartAsync()
    {
        _starting = true; UpdateButtons(); SetStatus("正在准备档案工具……");
        _client?.Disconnect(); _client = null;
        RenderAccount(null);
        var client = new MaaBanGBestdoriClient();
        try
        {
            await client.StartAsync(); _client = client;
            _catalog = await client.CallAsync("catalog"); BuildScope();
            _rows.Clear(); _cloudProfiles.Clear(); _cloud.ItemsSource = null; _password.Text = "";
            Dirty(); await RefreshAsync(); await RestoreReviewAsync(); _timer.Start();
        }
        catch { client.Disconnect(); _client = null; throw; }
        finally { _starting = false; UpdateButtons(); }
    }
    private async Task<JObject> Api(string action, JObject? body = null)
    {
        var value = await (_client ?? throw new InvalidOperationException("请先连接档案工具")).CallAsync(action, body ?? new JObject());
        if (value.Value<string>("message") is { } message) SetStatus(message);
        return value;
    }
    private async Task RefreshAsync()
    {
        var state = await _client!.CallAsync("state");
        _base.Text = $"底稿：{state["base_name"]} · {state["base_count"]} 张卡牌 · {(state.Value<int>("server") == 3 ? "中国服" : "其他服务器（不能扫描更新）")}";
        RenderAccount(state.Value<string>("username"));
        RenderCloudTarget(state["cloud_target"] as JObject);
        _baseCards = state["base_cards"] as JArray ?? new JArray();
        RefreshComparison();
        UpdateButtons();
        if (state.Value<bool>("busy")) SetStatus(state.Value<string>("message") ?? "正在读取游戏……");
        else if (_starting) SetStatus("档案工具已就绪，无需连接游戏即可导入、编辑和导出。");
    }
    private void SetStatus(string message, bool error = false)
    {
        _status.Text = message;
        _status.FontWeight = error ? FontWeight.SemiBold : FontWeight.Normal;
        _status.Foreground = new SolidColorBrush(Color.Parse(error ? "#991B1B" : "#1E3A8A"));
        _statusBanner.Background = new SolidColorBrush(Color.Parse(error ? "#FEE2E2" : "#DBEAFE"));
    }
    private void RenderAccount(string? username)
    {
        _loggedIn = !string.IsNullOrEmpty(username);
        _account.Text = _loggedIn ? "已登录：" + username : "尚未登录 Bestdori";
        _accountBadgeText.Text = _loggedIn ? "✓ Bestdori 已登录 · " + username : "Bestdori · 未登录";
        _accountBadgeText.Foreground = new SolidColorBrush(Color.Parse(_loggedIn ? "#166534" : "#475569"));
        _accountBadge.Background = new SolidColorBrush(Color.Parse(_loggedIn ? "#DCFCE7" : "#E2E8F0"));
        _loginForm.IsVisible = !_loggedIn;
        _logout.IsVisible = _loggedIn;
    }
    private void RenderCloudTarget(JObject? target)
    {
        _cloudTarget = target;
        _replaceTarget.Text = target == null
            ? "要替换已有档案，请先在“Bestdori 账号”中读取并使用云端底稿。"
            : $"替换目标：{target["name"]} · 账号 {target["account"]} · 第 {target.Value<int>("index") + 1} 个档案";
    }
    private void RefreshComparison()
    {
        var bases = _baseCards.OfType<JObject>().GroupBy(p => p.Value<int>("id")).ToDictionary(g => g.Key,g => g.ToList());
        var duplicateRows = _rows.Where(r => r.Selected).GroupBy(r => r.Id).Where(g => g.Count() > 1).Select(g => g.Key).ToHashSet();
        foreach (var row in _rows)
        {
            var known = int.TryParse(row.Id,out var id) && _catalog["cards"]?[row.Id] is JObject;
            var meta = known ? (JObject)_catalog["cards"]![row.Id]! : null;
            var inScope = meta != null && _members.TryGetValue(meta.Value<int>("characterId"),out var member) && member.IsChecked == true
                && _rarities.TryGetValue(meta.Value<int>("rarity"),out var rarity) && rarity.IsChecked == true
                && _attributes.TryGetValue(meta.Value<string>("attribute") ?? "",out var attribute) && attribute.IsChecked == true;
            bases.TryGetValue(id,out var baseline);
            row.SetComparison(baseline?.FirstOrDefault(), _policy.SelectedIndex == 0, inScope, baseline?.Count > 1 || duplicateRows.Contains(row.Id),
                meta?.Value<int>("levelLimit") ?? 60, meta?.Value<int>("trainingLevel") ?? 0);
        }
        // Refresh recreates rows and commits a focused TextBox on lost focus.
        // Property notifications already update numbers; rebuild only when the
        // visible membership changes, and defer filtering until editing ends.
        var visibleRows = _rows.Where(r => _onlyChanged.IsChecked != true || r.HasChanges);
        if (!_rowView.IsEditingItem && !_rowView.Cast<MaaBanGBestdoriRow>().SequenceEqual(visibleRows))
        {
            if (TopLevel.GetTopLevel(_table)?.FocusManager?.GetFocusedElement() is TextBox input
                && input.Name?.StartsWith("BestdoriTarget", StringComparison.Ordinal) == true)
            { _comparisonTimer.Stop(); _comparisonTimer.Start(); }
            else _rowView.Refresh();
        }
        _comparisonSummary.Text = $"共 {_rows.Count} 张 · 将变化 {_rows.Count(r => r.HasChanges)} 张";
    }
    private static string Local(JToken? names)
    {
        if (names is not JArray array) return names?.ToString() ?? "";
        return array.ElementAtOrDefault(3)?.Value<string>() ?? array.ElementAtOrDefault(1)?.Value<string>() ?? array.FirstOrDefault()?.Value<string>() ?? "";
    }
    private void BuildScope()
    {
        var selectedMembers = _members.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToHashSet();
        var selectedRarities = _rarities.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToHashSet();
        var selectedAttributes = _attributes.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToHashSet();
        bool first = _bands.Count == 0;
        _bands.Clear(); _members.Clear(); _rarities.Clear(); _attributes.Clear(); _scope.Children.Clear();
        _scope.Children.Add(Wrap(Button("全选成员", () => { SetMembers(_members.Keys, true); return Task.CompletedTask; }),
            Button("清空成员", () => { SetMembers(_members.Keys, false); return Task.CompletedTask; })));
        var gameOrder = new[] { 1,2,4,5,3,21,18,45 };
        foreach (var band in ((JObject)_catalog["bands"]!).Properties().OrderBy(p => Array.IndexOf(gameOrder,int.Parse(p.Name))))
        {
            var id = int.Parse(band.Name);
            var line = new Grid { ColumnDefinitions = new ColumnDefinitions("200,*,*,*,*,*"), MinWidth = 840, Margin = new Thickness(0,2,0,2) };
            var bandName = Text(Local(band.Value["bandName"])); bandName.FontWeight = FontWeight.SemiBold;
            var bandCheck = new MaaBanGBestdoriBandCheckBox { Content = "选择 5 人", Margin = new Thickness(0) };
            _bands[id] = bandCheck;
            bandCheck.IsCheckedChanged += (_, _) =>
            {
                if (!_updatingScope) SetMembers(MembersOfBand(id), bandCheck.IsChecked == true);
            };
            line.Children.Add(new StackPanel { Spacing = 2, Children = { bandName, bandCheck } });
            var column = 1;
            foreach (var member in ((JObject)_catalog["characters"]!).Properties().Where(p => p.Value.Value<int>("bandId") == id))
            {
                var memberId = int.Parse(member.Name);
                var shortName = memberId == 15 ? "美咲" : Local(member.Value[id == 18 ? "nickname" : "firstName"]);
                if (string.IsNullOrWhiteSpace(shortName)) shortName = Local(member.Value["characterName"]);
                var check = new CheckBox { Content = shortName, IsChecked = first || selectedMembers.Contains(memberId), Margin = new Thickness(4, 0), VerticalAlignment = VerticalAlignment.Center };
                _members[memberId] = check;
                check.IsCheckedChanged += (_, _) => { if (!_updatingScope) ScopeChanged(); };
                var tile = new Border { Child = check, CornerRadius = new CornerRadius(5), Padding = new Thickness(6,2), Margin = new Thickness(2), VerticalAlignment = VerticalAlignment.Center };
                void RenderMember() { tile.Background = new SolidColorBrush(Color.Parse(check.IsChecked == true ? "#FCE7F3" : "#F1F5F9")); check.Foreground = new SolidColorBrush(Color.Parse(check.IsChecked == true ? "#9D174D" : "#475569")); }
                check.IsCheckedChanged += (_,_) => RenderMember(); RenderMember();
                Grid.SetColumn(tile,column++); line.Children.Add(tile);
            }
            _scope.Children.Add(line);
        }
        _scope.Children.Add(Wrap(Enumerable.Range(1, 5).Select(n =>
        {
            var check = new CheckBox { Content = n + " 星", IsChecked = first || selectedRarities.Contains(n), Margin = new Thickness(0, 0, 14, 5) };
            _rarities[n] = check; check.IsCheckedChanged += (_, _) => ScopeChanged(); return (Control)check;
        }).ToArray()));
        _scope.Children.Add(Wrap(new Control[] { Text("种类") }.Concat(AttributeKinds.Select(kind =>
        {
            var check = new CheckBox { Content = char.ToUpperInvariant(kind[0]) + kind[1..], IsChecked = first || selectedAttributes.Contains(kind), Margin = new Thickness(0, 0, 14, 5) };
            _attributes[kind] = check; check.IsCheckedChanged += (_, _) => { if (!_updatingScope) ScopeChanged(); }; return (Control)check;
        })).ToArray()));
        ScopeChanged(); SearchCards();
    }
    private IEnumerable<int> MembersOfBand(int band) => ((JObject)_catalog["characters"]!).Properties()
        .Where(p => p.Value.Value<int>("bandId") == band).Select(p => int.Parse(p.Name));
    private void SetMembers(IEnumerable<int> members, bool selected)
    {
        _updatingScope = true;
        try { foreach (var id in members) _members[id].IsChecked = selected; }
        finally { _updatingScope = false; }
        ScopeChanged();
    }
    private void ScopeChanged()
    {
        _updatingScope = true;
        try
        {
            foreach (var (id, check) in _bands)
            {
                var members = MembersOfBand(id).ToList();
                var count = members.Count(member => _members[member].IsChecked == true);
                check.IsChecked = count == 0 ? false : count == members.Count ? true : null;
            }
        }
        finally { _updatingScope = false; }
        var selected = _members.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToArray();
        var stars = _rarities.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToArray();
        var attributes = _attributes.Where(p => p.Value.IsChecked == true).Select(p => p.Value.Content?.ToString()).ToArray();
        var empty = selected.Length == 0 || stars.Length == 0 || attributes.Length == 0;
        var text = selected.Length == 0 ? "请选择要读取的成员：勾选乐队可选中整队。" : stars.Length == 0 ? "请选择要读取的星级。" :
            attributes.Length == 0 ? "请选择要读取的种类。" :
            $"读取范围：{_bands.Count(p => p.Value.IsChecked != false)} 个乐队中的 {selected.Length} 位成员 · {string.Join("、", stars)} 星 · {string.Join("、", attributes)}";
        _scopeSummary.Text = _scanScope.Text = text;
        _scopeSummary.FontWeight = _scanScope.FontWeight = FontWeight.SemiBold;
        if (empty) _scopeSummary.Foreground = _scanScope.Foreground = Brushes.IndianRed;
        else { _scopeSummary.ClearValue(TextBlock.ForegroundProperty); _scanScope.ClearValue(TextBlock.ForegroundProperty); }
        Dirty();
        // Scope changes must update row comparisons even while a numeric editor
        // is active. RefreshComparison preserves that edit and defers row filtering.
        RefreshComparison();
        if (_rows.Count > 0) ScheduleCache();
    }
    private JObject Scope()
    {
        var members = _members.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToArray();
        var rarities = _rarities.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToArray();
        var attributes = _attributes.Where(p => p.Value.IsChecked == true).Select(p => p.Key).ToArray();
        if (members.Length == 0) throw new InvalidOperationException("请选择要读取的成员，勾选乐队可选中整队。");
        if (rarities.Length == 0) throw new InvalidOperationException("请选择要读取的星级。");
        if (attributes.Length == 0) throw new InvalidOperationException("请选择要读取的种类。");
        return new JObject
        {
            ["bands"] = new JArray(members.Select(id => _catalog["characters"]![id.ToString()]!.Value<int>("bandId")).Distinct()),
            ["members"] = new JArray(members), ["rarities"] = new JArray(rarities), ["attributes"] = new JArray(attributes),
        };
    }
    private string CardName(int id)
    {
        var card = _catalog["cards"]?[id.ToString()];
        if (card == null) return "未知卡牌";
        return Local(_catalog["characters"]?[card["characterId"]!.ToString()]?["characterName"]) + " · " + Local(card["prefix"]) + $" · {card["rarity"]} 星";
    }
    private record CardChoice(int Id, string Label) { public override string ToString() => Label; }
    private void SearchCards()
    {
        if (_catalog["cards"] is not JObject cards) return;
        var query = (_search.Text ?? "").Trim();
        _cardChoices.ItemsSource = cards.Properties().Select(p => new CardChoice(int.Parse(p.Name), p.Name + " · " + CardName(int.Parse(p.Name))))
            .Where(c => c.Label.Contains(query, StringComparison.OrdinalIgnoreCase)).Take(100).ToList();
        _cardChoices.SelectedIndex = 0;
    }
    private void AddRow(JObject value)
    {
        var id = value.Value<int?>("id");
        var row = new MaaBanGBestdoriRow { Id = id?.ToString() ?? "", Selected = value.Value<bool?>("selected") ?? (id != null && value.Value<bool?>("conflict") != true),
            NeedsReview = id == null || value.Value<bool?>("conflict") == true,
            Identity = id.HasValue ? CardName(id.Value) : value.Value<string>("identity") ?? (value.Value<string>("member") + " · " + value.Value<string>("title") + "（待确认）"),
            Level = value["level"]?.ToString() ?? "", Skill = value.Value<int?>("skill") is { } skill ? (skill + 1).ToString() : "",
            Master = value["master"]?.ToString() ?? "", Train = value["train"]?.ToString() ?? "", Warning = value.Value<string>("warning") ?? (id == null ? "请通过搜索确认卡牌身份" : "") };
        var original = value["original"] as JObject;
        if (original == null)
        {
            original = new JObject();
            foreach (var field in new[] { "level", "skill", "master", "train" })
                if (value[field]?.Type == JTokenType.Integer && value["maximize"]?.Value<bool>(field == "train" ? "level" : field) != true)
                    original[field] = value[field]!.DeepClone();
        }
        row.RememberOriginal(original);
        row.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(row.Id))
            {
                row.Identity = int.TryParse(row.Id, out var changedId) ? CardName(changedId) : "身份待确认";
                row.Artwork = row.NormalArtwork = null; row.ArtworkCaption = "卡面";
                _ = LoadMediaAsync(row);
            }
            if (e.PropertyName is nameof(row.Id) or nameof(row.Selected) or nameof(row.Level) or nameof(row.Skill) or nameof(row.Master) or nameof(row.Train)) { Dirty(); ScheduleCache(); }
        };
        if (value["maximize"] is JObject flags) ApplyMaximum(row,flags);
        _rows.Add(row); Dirty(); ScheduleCache(); _ = LoadMediaAsync(row);
    }
    private async Task LoadMediaAsync(MaaBanGBestdoriRow row)
    {
        if (!int.TryParse(row.Id, out var id) || _client == null) return;
        var client = _client; row.SkillText = "正在获取 Bestdori 技能资料……";
        await _mediaSlots.WaitAsync();
        try
        {
            if (_client != client || row.Id != id.ToString()) return;
            var data = await client.CallAsync("card", new JObject { ["id"] = id, ["trained"] = true, ["both"] = true });
            if (_client != client || row.Id != id.ToString()) return;
            row.SetSkillDetails(data.Value<string>("skill_name") ?? "", data["skill_descriptions"]?.Values<string>().Select(s => s ?? "").ToArray() ?? Array.Empty<string>());
            if (data.Value<string>("image") is { Length: > 0 } encoded)
            {
                using var stream = new MemoryStream(Convert.FromBase64String(encoded)); row.Artwork = new Bitmap(stream);
            }
            if (data["images"] is JArray images && images.Count > 1)
            {
                var normal=images.FirstOrDefault(v => v.Value<bool>("trained") == false)?.Value<string>("image");
                if (!string.IsNullOrEmpty(normal)) { using var stream=new MemoryStream(Convert.FromBase64String(normal));row.NormalArtwork=new Bitmap(stream); }
            }
            else row.NormalArtwork = null;
            row.ArtworkCaption = data["images"] is JArray variants && variants.Any(v => v.Value<bool>("trained")) ? "特训后" : "卡面";
            row.MediaError = data.Value<string>("image_error") ?? "";
        }
        catch { if (_client == client && row.Id == id.ToString()) { row.MediaError = "加载失败"; row.SkillText = "资料暂不可用，请点“重试加载小图与技能”"; } }
        finally { _mediaSlots.Release(); }
    }
    private void ScheduleCache()
    {
        if (_restoring) return;
        _cacheDirty = true; _cacheTimer.Stop(); _cacheTimer.Start();
    }
    private async Task SaveReviewAsync()
    {
        var rows = new JArray(_rows.Select(row =>
        {
            var value = new JObject { ["id"] = int.TryParse(row.Id,out var id) ? id : null,
                ["selected"] = row.Selected, ["conflict"] = row.NeedsReview, ["identity"] = row.Identity,
                ["warning"] = row.Warning,
                ["original"] = row.OriginalValues.DeepClone(),
                ["maximize"] = new JObject { ["level"] = row.MaxLevel, ["skill"] = row.MaxSkill, ["master"] = row.MaxMaster } };
            foreach (var (key,text,offset) in new[] { ("level",row.Level,0),("skill",row.Skill,-1),("master",row.Master,0),("train",row.Train,0) })
                if (int.TryParse(text,out var n)) value[key] = n+offset;
            return value;
        }));
        _cacheDirty = false;
        try { await _client!.CallAsync("cache_review",new JObject { ["observations"] = rows, ["scope"] = Scope() }); }
        catch { _cacheDirty = true; throw; }
    }
    private async Task RestoreReviewAsync()
    {
        var state = await _client!.CallAsync("state");
        if (state["observations"] is not JArray records || (records.Count == 0 && state.Value<string>("scan_status") == null)) return;
        _restoring = true;
        try
        {
            if (state["cached_scope"] is JObject scope)
            {
                var wanted = scope["members"]!.Values<int>().ToHashSet();
                _updatingScope = true;
                foreach (var (id,check) in _members) check.IsChecked = wanted.Contains(id);
                var stars = scope["rarities"]!.Values<int>().ToHashSet();
                foreach (var (id,check) in _rarities) check.IsChecked = stars.Contains(id);
                var attributes = scope["attributes"]?.Values<string>().ToHashSet() ?? AttributeKinds.ToHashSet();
                foreach (var (kind,check) in _attributes) check.IsChecked = attributes.Contains(kind);
                _updatingScope = false; ScopeChanged();
            }
            _rows.Clear(); foreach (var row in records) AddRow((JObject)row);
            _scanSummary.Text = $"已恢复缓存：{records.Count} 张 · {state["cached_at"]} · {(state.Value<string>("scan_status") == "complete" ? "上次完整读取" : "上次部分读取")}\n缓存不是游戏实时状态，可重新读取更新。";
            if (state["scan_summary"] is JObject summary && summary.Value<int>("existing_skipped") > 0)
                _scanSummary.Text += $"\n上次已跳过 {summary["existing_skipped"]} 张已有卡。";
            if (state["cache_recovery"] is JObject recovery)
                _scanSummary.Text += $"\n已从对应原始扫描补回 {recovery["fields"]} 项读取值，已修改的数值和勾选保持不变。";
        }
        finally { _restoring = false; }
    }
    private async Task ImportAsync(string text)
    {
        await Api("import", new JObject { ["text"] = text }); Dirty(); await RefreshAsync();
    }
    private async Task ImportFileAsync()
    {
        var top = TopLevel.GetTopLevel(this) ?? throw new IOException("无法打开文件选择器");
        var files = await top.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions { Title = "导入 Bestdori 资料", AllowMultiple = false });
        if (files.Count == 0) return;
        await using var stream = await files[0].OpenReadAsync();
        if (stream.CanSeek && stream.Length > 4 * 1024 * 1024) throw new IOException("资料超过 4 MB");
        using var reader = new StreamReader(stream, Encoding.UTF8); await ImportAsync(await reader.ReadToEndAsync());
    }
    private async Task LoginAsync()
    {
        var password = _password.Text ?? ""; _password.Text = "";
        try { await Api("login", new JObject { ["username"] = _username.Text, ["password"] = password }); _cloudProfiles.Clear(); _cloud.ItemsSource = null; }
        finally { password = ""; await RefreshAsync(); }
    }
    private async Task CloudAsync()
    {
        var result = await Api("cloud"); _cloudProfiles = (JArray)result["profiles"]!;
        _cloud.ItemsSource = _cloudProfiles.Select(p => p.Value<string>("name")).ToList(); _cloud.SelectedIndex = _cloudProfiles.Count > 0 ? 0 : -1;
    }
    private async Task ScanAsync()
    {
        JObject scope;
        try { scope = Scope(); }
        catch (Exception ex) { _scanSummary.Text = "未开始读取：" + ex.Message; throw; }
        var vm = Instances.InstanceTabBarViewModel.ActiveTab?.TaskQueueViewModel ?? throw new InvalidOperationException("请先在主页选择设备");
        if (!vm.Idle || vm.Processor.IsTaskRunActive || vm.Processor.IsConnecting)
            throw new InvalidOperationException("当前设备正在执行任务或连接，请等待结束后再读取");
        SetStatus("正在准备读取游戏……"); _scanSummary.Text = "正在准备读取游戏……";
        var targets = Targets();
        // Original values are needed for max(original, target) and one-click undo.
        var start = await Api("scan", new JObject { ["scope"] = scope, ["maximize"] = new JObject(), ["skip_existing"] = _skipExisting.IsChecked == true });
        var scanId = start.Value<string>("scan_id")!;
        _scanning = true; _scanCancellation = new CancellationTokenSource(); Dirty(); UpdateButtons();
        string? error = null;
        try { await vm.Processor.RunBestdoriScanAsync(scanId, _scanCancellation.Token); }
        catch (OperationCanceledException) { error = "已停止读取"; }
        catch (Exception ex) { error = ex.Message; }
        finally
        {
            try
            {
                await Api("scan_finish", new JObject { ["scan_id"] = scanId, ["error"] = error });
                var state = await _client!.CallAsync("state"); _rows.Clear();
                foreach (var row in (JArray)state["observations"]!)
                {
                    var value = (JObject)row;
                    AddRow(value);
                }
                var missing = ApplyTargets(_rows.Where(r => r.Selected), targets);
                _scanSummary.Text = $"{(state.Value<string>("scan_status") == "complete" ? "扫描完成" : "部分读取")} · {_rows.Count} 张记录\n" + string.Join("\n", ((JArray)state["scan_errors"]!).Select(v => v.ToString()));
                if (state["scan_summary"] is JObject summary && summary.Value<int>("existing_skipped") > 0)
                    _scanSummary.Text += $"\n已跳过 {summary["existing_skipped"]} 张已有卡，其中 {summary["thumbnail_skipped"]} 次在列表小图确认。";
                if (missing > 0) _scanSummary.Text += $"\n{missing} 项数值未读到，未应用提升。";
                await SaveReviewAsync();
            }
            finally { _scanning = false; _scanCancellation.Dispose(); _scanCancellation = null; UpdateButtons(); }
        }
    }
    private async Task PreviewAsync()
    {
        if (!_table.CommitEdit(DataGridEditingUnit.Cell, true) || !_table.CommitEdit(DataGridEditingUnit.Row, true))
            throw new InvalidOperationException("请完成表格中的修改");
        await SaveReviewAsync();
        RefreshComparison();
        var body = new JObject { ["scope"] = Scope(), ["policy"] = _policy.SelectedIndex == 0 ? "max" : "overwrite", ["name"] = _name.Text,
            ["observations"] = new JArray(_rows.Where(r => r.Selected).Select(r => r.Observation())) };
        var revision = _revision;
        await Api("preview", body); var state = await _client!.CallAsync("state");
        if (revision != _revision) throw new InvalidOperationException("设置已更改，请重新生成预览");
        _previewToken = state.Value<string>("result_token"); var result = state["result"]!;
        _previewSummary.Text = $"{_name.Text} · 新增 {result["added"]} 张 · 更新 {result["updated"]} 张 · 待处理 {((JArray)result["skipped"]!).Count} 张";
        _preview.Children.Clear();
        foreach (var change in (JArray)result["changes"]!)
        {
            var before = change["before"] as JObject;
            var after = (JObject)change["after"]!;
            var heading = Text($"{(before == null ? "新增" : "更新")} · {CardName(change.Value<int>("id"))} · ID {change["id"]}");
            heading.FontWeight = FontWeight.SemiBold;
            var values = new WrapPanel();
            foreach (var (field, label, offset) in new[] { ("level", "等级", 0), ("skill", "技能", 1), ("master", "星光", 0), ("train", "特训", 0) })
            {
                var oldValue = before?.Value<int>(field) + offset;
                var newValue = after.Value<int>(field) + offset;
                var changed = oldValue != newValue;
                var lower = oldValue.HasValue && newValue < oldValue;
                var text = Text(changed ? $"{label} {oldValue?.ToString() ?? "—"} → {newValue}" : $"{label} {newValue}");
                text.Foreground = new SolidColorBrush(Color.Parse(!changed ? "#64748B" : lower ? "#92400E" : "#166534"));
                text.FontWeight = changed ? FontWeight.SemiBold : FontWeight.Normal;
                values.Children.Add(new Border { Child = text, Padding = new Thickness(9, 5), Margin = new Thickness(0, 3, 8, 3), CornerRadius = new CornerRadius(4),
                    Background = changed ? new SolidColorBrush(Color.Parse(lower ? "#FEF3C7" : "#DCFCE7")) : Brushes.Transparent });
            }
            _preview.Children.Add(new Border { Padding = new Thickness(12, 8), BorderThickness = new Thickness(0, 0, 0, 1),
                BorderBrush = new SolidColorBrush(Color.Parse("#DCE2EB")), Child = Stack(heading, values) });
        }
        foreach (var skip in (JArray)result["skipped"]!) _preview.Children.Add(Text($"待处理 #{skip["id"]}：{skip["reason"]}"));
        _tabs.SelectedIndex = 2;
    }
    private JObject PreviewRequest() => new() { ["token"] = _previewToken ?? throw new InvalidOperationException("请先生成合并预览") };
    private async Task ExportAsync()
    {
        var result = await Api("export", PreviewRequest());
        var top = TopLevel.GetTopLevel(this)!;
        var file = await top.StorageProvider.SaveFilePickerAsync(new FilePickerSaveOptions { Title = "导出 Bestdori 资料", SuggestedFileName = result.Value<string>("filename"), DefaultExtension = "json" });
        if (file == null) return;
        await using var stream = await file.OpenWriteAsync(); stream.SetLength(0);
        await using var writer = new StreamWriter(stream, new UTF8Encoding(false)); await writer.WriteAsync(result.Value<string>("text"));
        SetStatus("资料已导出，可在 Bestdori 的资料管理中导入。");
    }
    private async Task CopyAsync()
    {
        var result = await Api("export", PreviewRequest());
        var clipboard = TopLevel.GetTopLevel(this)?.Clipboard ?? throw new IOException("剪贴板不可用");
        await clipboard.SetTextAsync(result.Value<string>("text")); SetStatus("资料已复制，可在 Bestdori 中导入。");
    }
    private async Task UploadAsync() => await Api("upload", PreviewRequest());
    private async Task ReplaceAsync()
    {
        var body = PreviewRequest();
        body["target"] = _cloudTarget?.DeepClone() ?? throw new InvalidOperationException("请先使用云端底稿");
        try { await Api("replace", body); }
        finally
        {
            _cloudProfiles.Clear(); _cloud.ItemsSource = null;
            await RefreshAsync();
        }
    }
}
