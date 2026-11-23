export function inferSourceMonths(name) {
  const text=String(name || '').replace(/\s+/g,''), result=new Set()
  for (const match of text.matchAll(/(?<!\d)(\d{1,2}(?:月?[+＋、,，/&和至到\-–—~～]\d{1,2})*)月/g)) {
    for (const part of match[1].replaceAll('月','').split(/[+＋、,，/&和]/)) {
      const bounds=part.split(/[至到\-–—~～]/).map(Number)
      if(bounds.some(n=>n<1||n>12)||bounds.length>2)return []
      if(bounds.length===2){if(bounds[0]>bounds[1])return [];for(let n=bounds[0];n<=bounds[1];n++)result.add(n)}
      else result.add(bounds[0])
    }
  }
  return [...result].sort((a,b)=>a-b)
}
