import re
def split_rules(css):
    """Split top-level CSS into [(prelude, body)]; @media bodies are kept raw."""
    out=[]; i=0; n=len(css)
    css=re.sub(r'/\*.*?\*/','',css,flags=re.S); n=len(css)
    while i<n:
        j=css.find('{',i)
        if j<0: break
        pre=css[i:j].strip(); depth=1; k=j+1
        while k<n and depth:
            if css[k]=='{': depth+=1
            elif css[k]=='}': depth-=1
            k+=1
        out.append((pre,css[j+1:k-1])); i=k
    return out
