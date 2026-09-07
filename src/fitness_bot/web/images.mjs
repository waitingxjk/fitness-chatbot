export function validateImages(files, currentCount = 0) {
  if (files.length + currentCount > 3) throw new Error('每条消息最多 3 张图片，请先移除已有图片。');
  for (const file of files) {
    if (!['image/jpeg','image/png','image/webp'].includes(file.type)) throw new Error('仅支持 JPEG、PNG、WebP；请先将 HEIC 等格式转换后上传。');
    if (!file.size || file.size > 5 * 1024 * 1024) throw new Error('每张图片需大于 0 且不超过 5 MiB。');
  }
}
export async function uploadImage(file, fetcher = fetch) {
  const response = await fetcher('/v1/images',{method:'POST',headers:{'Content-Type':file.type},body:file,signal:AbortSignal.timeout(30000)});
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* HTTP code remains available */ }
    throw new Error(typeof detail === 'string' ? detail : `上传失败（HTTP ${response.status}）`);
  }
  return response.json();
}

export function clipboardImages(data) {
  if (!data) return [];
  const images = [...(data.items ?? [])]
    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
    .map(item => item.getAsFile()).filter(Boolean);
  return images.length ? images : [...(data.files ?? [])].filter(file => file.type.startsWith('image/'));
}
