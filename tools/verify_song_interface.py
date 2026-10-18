"""Exercise the actual compiled UI catalog loader against reference/legacy options.

No game, user profile, or application window is opened. Runs in Windows package CI.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape

from song_interface import ROOT, bindings, expand_interface, install_bindings
from build_ui import REVISION

PROGRAM = r'''
using MFAAvalonia.Helper;
using MFAAvalonia.Extensions.MaaFW;
using Newtonsoft.Json.Linq;
using Option = MFAAvalonia.Extensions.MaaFW.MaaInterface.MaaInterfaceSelectOption;
using Case = MFAAvalonia.Extensions.MaaFW.MaaInterface.MaaInterfaceOptionCase;
// Reparent old versus choices before initializing new defaults; preserve the
// selection objects, inactive-mode settings, and later edits on repeated loads.
foreach (var activeMode in new[] { 4, 6 })
foreach (var alreadyPromoted in new[] { false, true })
{
    var song = new Option { Name = "谱面竞演歌曲", Index = 17 };
    var favorites = new Option { Name = "谱面从收藏选择", Index = 1 };
    var room = new Option { Name = "谱面竞演房间", Index = 1, SubOptions = [song, favorites] };
    var mode = new Option { Name = "谱面演出模式", Index = activeMode, SubOptions = [room] };
    var existing = new Option { Name = song.Name, Index = 28 };
    var existingFavorites = new Option { Name = favorites.Name, Index = 0 };
    if (alreadyPromoted) mode.SubOptions.AddRange([existing, existingFavorites]);
    List<Option> options = [mode];
    MaaBanGSongCatalog.MigrateOnlineSelections(options);
    if (!ReferenceEquals(mode.SubOptions.Find(o => o.Name == song.Name), alreadyPromoted ? existing : song)
        || !ReferenceEquals(mode.SubOptions.Find(o => o.Name == favorites.Name),
            alreadyPromoted && activeMode != 6 ? existingFavorites : favorites)
        || room.SubOptions.Count != 0) throw new Exception("Online option migration lost a saved selection");
    var moved = mode.SubOptions.Find(o => o.Name == song.Name)!;
    moved.Index = 39;
    MaaBanGSongCatalog.MigrateOnlineSelections(options);
    if (moved.Index != 39 || mode.SubOptions.Count != 3) throw new Exception("Online option migration is not idempotent");
}
MaaBanGSongCatalog.MigrateOnlineSelections(null);
Console.WriteLine("Online option migration: 4 fixtures passed");
var results = new JArray();
foreach (JObject test in JArray.Parse(File.ReadAllText(args[0])))
{
    try
    {
        var result = test["path"] != null
            ? MaaBanGSongCatalog.LoadOptions(test["path"]!.Value<string>()!)
            : MaaBanGSongCatalog.Expand((JObject)test["source"]!, (JObject)test["spec"]!, (JObject)test["catalog"]!);
        foreach (var name in new[] { "谱面协力歌曲", "谱面竞演歌曲" })
        {
            if (result == null) continue;
            var cases = result[name]!["cases"]!.ToObject<List<Case>>()!;
            var original = cases.ToArray();
            var display = MaaBanGSongCatalog.DisplayCases(name, cases)!;
            if (display[0].Name != "不指定歌曲" || display[1].Name != "选择当前歌曲"
                || !cases.SequenceEqual(original) || !display.ToHashSet().SetEquals(cases))
                throw new Exception("Online song display order changed stored cases");
            // Opening and choosing each displayed item must resolve to its old
            // stored index, including current-song and every concrete song.
            for (var index = 0; index < cases.Count; index++)
                if (!ReferenceEquals(display.Find(c => c.Name == cases[index].Name), cases[index])
                    || cases.FindIndex(c => c.Name == display.Find(d => ReferenceEquals(d, cases[index]))!.Name) != index)
                    throw new Exception("Online song selection round-trip changed saved index");
            if (!ReferenceEquals(MaaBanGSongCatalog.DisplayCases("演出歌曲", cases), cases))
                throw new Exception("Unrelated song selector order changed");
        }
        results.Add(new JObject { ["options"] = result });
    }
    catch (Exception e) { results.Add(new JObject { ["error"] = e.GetType().Name }); }
}
File.WriteAllText(args[1], results.ToString());
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-build', type=Path, default=ROOT/'deps/release-cache/ui-build')
    parser.add_argument('--legacy-interface', type=Path)
    args = parser.parse_args()
    ui = args.ui_build.resolve()
    source = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
    catalog = json.loads((ROOT/'agent/data/songs_cn.json').read_text(encoding='utf8'))
    spec = bindings()
    expected = expand_interface(source, catalog, spec)['option']
    if args.legacy_interface:
        legacy = json.loads(args.legacy_interface.read_text(encoding='utf8'))
        assert expected == legacy['option'], 'Legacy case indices, identities or difficulty options changed'
    with tempfile.TemporaryDirectory(prefix='song-interface-', dir=ROOT/'deps') as folder:
        work = Path(folder)
        # Library builds do not copy NuGet dependencies. Resolve the exact JSON
        # assembly used by the pinned UI build rather than installing a second version.
        assets = json.loads((ROOT/'deps/release-cache/ui-source'/f'MFAAvalonia-{REVISION}'/
                             'MFAAvalonia/obj/project.assets.json').read_text(encoding='utf8'))
        dependencies = []
        for name in ('Newtonsoft.Json', 'CommunityToolkit.Mvvm'):
            key = next(k for k in assets['libraries'] if k.startswith(name + '/'))
            runtime = next(target[key]['runtime'] for target in assets['targets'].values()
                           if key in target and 'runtime' in target[key])
            relative = next(p for p in runtime if p.endswith('/' + name + '.dll'))
            dependencies.append(next(Path(folder)/assets['libraries'][key]['path']/relative
                                     for folder in assets['packageFolders']
                                     if (Path(folder)/assets['libraries'][key]['path']/relative).is_file()))
        references = '\n'.join(f'<Reference Include="{path.stem}"><HintPath>{escape(str(path))}</HintPath></Reference>'
                               for path in (ui/'MFAAvalonia.Core.dll', *dependencies))
        (work/'Verify.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework>
<ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup>
<ItemGroup>{references}</ItemGroup></Project>''', encoding='utf8')
        (work/'Program.cs').write_text(PROGRAM, encoding='utf8')
        # This harness only references local assemblies and SDK packs.
        config=work/'NuGet.Config'
        config.write_text('<configuration><packageSources><clear /></packageSources></configuration>',encoding='utf8')
        package = work/'app'
        (package/'agent/data').mkdir(parents=True)
        shutil.copy2(ROOT/'assets/interface.json', package/'interface.json')
        shutil.copy2(ROOT/'agent/data/songs_cn.json', package/'agent/data/songs_cn.json')
        install_bindings(package)
        broken = work/'broken'
        broken.mkdir()
        shutil.copy2(ROOT/'assets/interface.json', broken/'interface.json')
        legacy_dir = work/'legacy'
        legacy_dir.mkdir()
        expanded = dict(source, option=expected)
        (legacy_dir/'interface.json').write_text(json.dumps(expanded), encoding='utf8')
        fixtures = [dict(path=str(ROOT/'assets/interface.json')), dict(path=str(package/'interface.json')),
                    dict(path=str(broken/'interface.json')), dict(path=str(legacy_dir/'interface.json'))]
        outputs = [dict(options=expected), dict(options=expected), dict(error='InvalidDataException'), dict(options=None)]
        # Refresh/ordering/difficulty/duplicate-title fixtures use the compiled loader.
        changed = deepcopy(catalog)
        changed['songs'] = [deepcopy(catalog['songs'][0]), deepcopy(catalog['songs'][1])]
        changed['songs'][0]['title'] = changed['songs'][1]['title'] = '同名曲'
        changed['songs'][0]['difficulties']['special'] = {'available': False}
        changed['songs'][1]['difficulties']['special'] = {'available': True}
        changed['songs'].append(dict(deepcopy(changed['songs'][0]), id='99999', active=False))
        for value in (changed, dict(changed, songs=list(reversed(changed['songs'])))):
            fixtures.append(dict(source=source, spec=spec, catalog=value))
            outputs.append(dict(options=expand_interface(source, value, spec)['option']))
        for value in (dict(catalog, server='all'), dict(catalog, songs=[])):
            fixtures.append(dict(source=source, spec=spec, catalog=value))
            outputs.append(dict(error='InvalidDataException'))
        input_path, output_path = work/'input.json', work/'output.json'
        input_path.write_text(json.dumps(fixtures, ensure_ascii=False), encoding='utf8')
        dotnet = os.environ.get('MAABANG_DOTNET', 'dotnet')
        subprocess.run([dotnet, 'build', str(work/'Verify.csproj'), '-c', 'Release', '-o', str(work/'out'),
                        '--configfile',str(config),'--verbosity', 'quiet'], check=True)
        subprocess.run([dotnet, str(work/'out/Verify.dll'), str(input_path), str(output_path)], check=True)
        results = json.loads(output_path.read_text(encoding='utf8'))
        assert len(results) == len(outputs)
        for index, (result, wanted) in enumerate(zip(results, outputs)):
            assert result == wanted, f'Compiled UI catalog fixture {index} did not match'
        print(f'Compiled UI catalog loader: {len(fixtures)} fixtures passed; {len(expected)} options verified')


if __name__ == '__main__':
    main()
