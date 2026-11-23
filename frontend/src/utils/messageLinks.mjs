// 只生成 HTTP(S) 链接；其余消息始终作为文本渲染。
export function messageLinks(value) {
  const text=String(value ?? ''),parts=[]
  const pattern=/\[([^\]\n]+)\]\((https?:\/\/(?:[^\s<>"'`()\[\]]|\([^()\s<>]*\))+)\)|(https?:\/\/[^\s<>"'`，。；！？、【】「」《》]+|\bwww\.[^\s<>"'`，。；！？、【】「」《》]+)/gi
  let cursor=0
  for(const match of text.matchAll(pattern)){
    let raw=match[2] || match[3],end=match.index+match[0].length
    if(!match[2]){
      raw=raw.replace(/[.,;!?，。；！？]+$/u,'')
      while(raw.endsWith(')') && (raw.match(/\)/g)||[]).length>(raw.match(/\(/g)||[]).length)raw=raw.slice(0,-1)
      while(raw.endsWith(']') && (raw.match(/\]/g)||[]).length>(raw.match(/\[/g)||[]).length)raw=raw.slice(0,-1)
      end=match.index+raw.length
    }
    let url
    try{url=new URL(/^www\./i.test(raw)?`https://${raw}`:raw)}catch{continue}
    if(!['http:','https:'].includes(url.protocol)||!url.hostname)continue
    if(match.index>cursor)parts.push({text:text.slice(cursor,match.index)})
    parts.push({text:match[1] || raw,href:url.href})
    cursor=end
  }
  if(cursor<text.length)parts.push({text:text.slice(cursor)})
  return parts
}
