import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { configApi } from '../api/index.js'
import { ElMessage } from 'element-plus'

export const useConfigStore = defineStore('config', () => {
  const platforms = ref([])
  const shops = ref([])
  const warehouses = ref([])
  const calendar = ref([])
  const loading = ref(false)
  const loaded = ref(false)
  let fetchPromise = null

  const allChannels = computed(() => {
    const result = []
    for (const p of platforms.value) {
      for (const ch of p.channels || []) {
        result.push({ ...ch, platform_id: p.platform_id, platform_name: p.name })
      }
    }
    return result
  })

  async function fetchAll({ force = false } = {}) {
    if (loaded.value && !force) return
    if (fetchPromise) return fetchPromise

    loading.value = true
    fetchPromise = configApi.bootstrap().then((snapshot) => {
        platforms.value = snapshot.platforms || []
        shops.value = snapshot.shops || []
        warehouses.value = snapshot.warehouses || []
        calendar.value = snapshot.calendar || []
        loaded.value = true
      }).finally(() => {
        loading.value = false
        fetchPromise = null
      })
    return fetchPromise
  }

  async function createPlatform(data) {
    const res = await configApi.createPlatform(data)
    platforms.value.push(res)
    ElMessage.success('平台已创建')
    return res
  }

  async function updatePlatform(platformId, data) {
    const res = await configApi.updatePlatform(platformId, data)
    const idx = platforms.value.findIndex(p => p.platform_id === platformId)
    if (idx !== -1) platforms.value[idx] = res
    ElMessage.success('平台已更新')
    return res
  }

  async function deletePlatform(platformId) {
    await configApi.deletePlatform(platformId)
    platforms.value = platforms.value.filter(p => p.platform_id !== platformId)
    ElMessage.success('平台已删除')
  }

  async function createShop(data) {
    const res = await configApi.createShop(data)
    shops.value.push(res)
    ElMessage.success('店铺已创建')
    return res
  }

  async function updateShop(shopId, data) {
    const res = await configApi.updateShop(shopId, data)
    const idx = shops.value.findIndex(s => s.shop_id === shopId)
    if (idx !== -1) shops.value[idx] = res
    ElMessage.success('店铺已更新')
  }

  async function deleteShop(shopId) {
    await configApi.deleteShop(shopId)
    shops.value = shops.value.filter(s => s.shop_id !== shopId)
    ElMessage.success('店铺已删除')
  }

  async function createWarehouse(data) {
    const res = await configApi.createWarehouse(data)
    warehouses.value.push(res)
    ElMessage.success('仓库已创建')
    return res
  }

  async function updateWarehouse(warehouseId, data) {
    const res = await configApi.updateWarehouse(warehouseId, data)
    const idx = warehouses.value.findIndex(w => w.warehouse_id === warehouseId)
    if (idx !== -1) warehouses.value[idx] = res
    ElMessage.success('仓库已更新')
  }

  async function deleteWarehouse(warehouseId) {
    await configApi.deleteWarehouse(warehouseId)
    warehouses.value = warehouses.value.filter(w => w.warehouse_id !== warehouseId)
    ElMessage.success('仓库已删除')
  }

  async function createCalendar(data) {
    const res = await configApi.createCalendar(data)
    calendar.value.push(res)
    ElMessage.success('大促日历已添加')
    return res
  }

  async function updateCalendar(id, data) {
    const res = await configApi.updateCalendar(id, data)
    const idx = calendar.value.findIndex(c => c.id === id)
    if (idx !== -1) calendar.value[idx] = res
    ElMessage.success('大促日历已更新')
    return res
  }

  async function deleteCalendar(id) {
    await configApi.deleteCalendar(id)
    calendar.value = calendar.value.filter(c => c.id !== id)
    ElMessage.success('已删除')
  }

  return {
    platforms, shops, warehouses, calendar, loading, loaded, allChannels,
    fetchAll,
    createPlatform, updatePlatform, deletePlatform,
    createShop, updateShop, deleteShop,
    createWarehouse, updateWarehouse, deleteWarehouse,
    createCalendar, updateCalendar, deleteCalendar,
  }
})
