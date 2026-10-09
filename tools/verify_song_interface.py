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
using Newtonsoft.Json.Linq;
var results = new JArray();
foreach (JObject test in JArray.Parse(File.ReadAllText(args[0])))
{
    try
    {
        var result = test["path"] != null
            ? MaaBanGSongCatalog.LoadOptions(test["path"]!.Value<string>()!)
            : MaaBanGSongCatalog.Expand((JObject)test["source"]!, (JObject)test["spec"]!, (JObject)test["catalog"]!);
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
        key = next(k for k in assets['libraries'] if k.startswith('Newtonsoft.Json/'))
        runtime = next(target[key]['runtime'] for target in assets['targets'].values()
                       if key in target and 'runtime' in target[key])
        relative = next(p for p in runtime if p.endswith('/Newtonsoft.Json.dll'))
        dependency = next(Path(folder)/assets['libraries'][key]['path']/relative
                          for folder in assets['packageFolders']
                          if (Path(folder)/assets['libraries'][key]['path']/relative).is_file())
        references = '\n'.join(f'<Reference Include="{path.stem}"><HintPath>{escape(str(path))}</HintPath></Reference>'
                               for path in (ui/'MFAAvalonia.Core.dll', dependency))
        (work/'Verify.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework>
<ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup>
<ItemGroup>{references}</ItemGroup></Project>''', encoding='utf8')
        (work/'Program.cs').write_text(PROGRAM, encoding='utf8')
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
                        '--verbosity', 'quiet'], check=True)
        subprocess.run([dotnet, str(work/'out/Verify.dll'), str(input_path), str(output_path)], check=True)
        results = json.loads(output_path.read_text(encoding='utf8'))
        assert len(results) == len(outputs)
        for index, (result, wanted) in enumerate(zip(results, outputs)):
            assert result == wanted, f'Compiled UI catalog fixture {index} did not match'
        print(f'Compiled UI catalog loader: {len(fixtures)} fixtures passed; {len(expected)} options verified')


if __name__ == '__main__':
    main()
