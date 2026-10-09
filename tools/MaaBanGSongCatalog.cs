using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Helper;

/// <summary>Materialize independent PI selectors from one offline CN catalog.</summary>
public static class MaaBanGSongCatalog
{
    private static readonly string[] Difficulties = ["easy", "normal", "hard", "expert", "special"];

    public static JObject? LoadOptions(string interfacePath)
    {
        var specPath = Path.ChangeExtension(interfacePath, ".songs.json");
        var source = JObject.Parse(File.ReadAllText(interfacePath));
        if (!File.Exists(specPath))
        {
            // Legacy expanded interfaces remain supported. A missing binding file
            // must never silently present empty choices with stale pipeline defaults.
            if (source["name"]?.Value<string>() == "MaaBanG" &&
                source["option"]?["演出歌曲"]?["cases"] is JArray { Count: 0 })
                throw new InvalidDataException("歌曲选项绑定文件缺失：" + specPath);
            return null;
        }
        var spec = JObject.Parse(File.ReadAllText(specPath));
        var catalogPath = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(interfacePath)!, Required(spec, "catalog")));
        return Expand(source, spec, JObject.Parse(File.ReadAllText(catalogPath)));
    }

    private static string Required(JObject value, string key) =>
        value[key]?.Type == JTokenType.String && !string.IsNullOrEmpty(value[key]!.Value<string>())
            ? value[key]!.Value<string>()!
            : throw new InvalidDataException("歌曲配置缺少字段：" + key);

    private static JObject Override(string node, string value) =>
        new() { [node] = new JObject { ["attach"] = new JObject { ["value"] = value } } };

    public static JObject Expand(JObject source, JObject spec, JObject catalog)
    {
        if (spec["version"]?.Value<int>() != 1 || catalog["server"]?.Value<string>() != "cn")
            throw new InvalidDataException("歌曲选项需要 v1 绑定配置和国服曲库");
        var songs = ((JArray?)catalog["songs"] ?? throw new InvalidDataException("曲库缺少 songs"))
            .Cast<JObject>().Where(s => s["active"]?.Value<bool>() == true &&
                s["difficulties"]?["expert"]?["available"]?.Value<bool>() == true).ToList();
        if (songs.Count == 0) throw new InvalidDataException("国服可选曲库为空");
        if (songs.Select(s => Required(s, "id")).Distinct(StringComparer.Ordinal).Count() != songs.Count)
            throw new InvalidDataException("国服曲库包含重复歌曲 ID");
        var titles = songs.GroupBy(s => Required(s, "title"), StringComparer.Ordinal)
            .ToDictionary(g => g.Key, g => g.Count(), StringComparer.Ordinal);
        var chartSongs = songs.OrderBy(s => Required(s, "title") == "SAVIOR OF SONG" ? 0 : 1)
            .ThenBy(s => long.Parse(Required(s, "id"))).ToList();
        var options = (JObject?)source["option"]?.DeepClone() ?? throw new InvalidDataException("界面缺少 option");
        var bindings = (JObject?)spec["selectors"] ?? throw new InvalidDataException("歌曲配置缺少 selectors");
        foreach (var entry in bindings.Properties())
        {
            var binding = (JObject)entry.Value;
            var node = Required(binding, "song_node");
            var order = Required(binding, "order");
            var valueKind = Required(binding, "value");
            if (order != "catalog" && order != "chart" || valueKind != "id" && valueKind != "key")
                throw new InvalidDataException("不支持的歌曲排序或参数类型：" + entry.Name);
            var option = (JObject?)options[entry.Name] ?? throw new InvalidDataException("界面缺少选歌入口：" + entry.Name);
            option["default_case"] ??= Required(spec, "default_case");
            var cases = new JArray(((JArray?)option["cases"] ?? throw new InvalidDataException("选项缺少 cases"))
                .Where(c => c["pipeline_override"]?[node]?["attach"]?["value"]?.Value<string>() == "")
                .Select(c => c.DeepClone()));
            var prefix = binding["difficulty_prefix"]?.Value<string>();
            if (prefix != null)
                foreach (var key in options.Properties().Where(p => p.Name.StartsWith(prefix, StringComparison.Ordinal)).ToList())
                    key.Remove();
            foreach (var song in order == "catalog" ? songs : chartSongs)
            {
                var title = Required(song, "title");
                var id = Required(song, "id");
                var key = titles[title] == 1 ? title : $"{title} [{id}]";
                var item = new JObject {
                    ["name"] = key, ["label"] = title + " · " + song["band"]?.Value<string>(),
                    ["pipeline_override"] = Override(node, valueKind == "key" ? key : id)
                };
                if (prefix != null)
                {
                    var names = Difficulties.Where(d => song["difficulties"]?[d]?["available"]?.Value<bool>() == true).ToArray();
                    var profile = prefix + string.Join("_", names);
                    options[profile] = new JObject {
                        ["type"] = "select", ["label"] = Required(option, "label").Replace("歌曲", "难度"),
                        ["default_case"] = "EXPERT", ["cases"] = new JArray(names.Select(d => new JObject {
                            ["name"] = d.ToUpperInvariant(),
                            ["pipeline_override"] = Override(Required(binding, "difficulty_node"), d)
                        }))
                    };
                    item["option"] = new JArray(profile);
                }
                cases.Add(item);
            }
            option["cases"] = cases;
            if (!cases.Any(c => c["name"]?.Value<string>() == option["default_case"]?.Value<string>()))
                option["default_case"] = cases[0]["name"]!.DeepClone();
        }
        if (options.Properties().Any(p => p.Value["type"]?.Value<string>() == "select" &&
            p.Value["cases"] is JArray { Count: 0 }))
            throw new InvalidDataException("歌曲选项绑定不完整，存在空的选歌入口");
        return options;
    }
}
