"""One OCR pass for online room errors, shared by setup and playback."""
import re


def room_error(context, recognize, *, fatal_only=False):
    names=('OL_Fatal','OL_Disconnected')
    nodes=[context.get_node_data(name) for name in names]
    if all(node and node.get('enabled',True) and not node.get('inverse',False)
           and node.get('recognition',{}).get('type')=='OCR' for node in nodes):
        params=[node['recognition']['param'] for node in nodes]
        shared=[{k:v for k,v in p.items() if k!='expected'} for p in params]
        # Different thresholds/ROIs or indexed matches retain their own semantics.
        if (shared[0]==shared[1] and params[0].get('index',0)==0
                and all(p.get('expected') for p in params)):
            patterns=[p['expected'] for p in params]
            result=recognize(names[0],expected=patterns[0]+patterns[1])
            if result:
                fatal=next((hit for hit in result.filtered_results
                            if any(re.search(p,hit.text) for p in patterns[0])),None)
                if fatal:
                    return names[0],str(fatal.text)
                if not fatal_only:
                    return names[1],str(result.best_result.text)
            return None
    # Inactive snapshots previously checked only fatal errors. If the nodes
    # cannot share OCR, leave disconnect recognition until a caller needs it.
    for name in names[:1] if fatal_only else names:
        result=recognize(name)
        if result:
            return name,str(result.best_result.text)
    return None
