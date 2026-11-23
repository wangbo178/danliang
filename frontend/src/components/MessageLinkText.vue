<template><span><template v-for="(part,index) in parts" :key="index"><a v-if="part.href" :href="part.href" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">{{ part.text }}</a><template v-else>{{ part.text }}</template></template></span></template>
<script setup>
import {computed} from 'vue'
import {messageLinks} from '../utils/messageLinks.mjs'
const props=defineProps({text:{type:String,default:''}})
const parts=computed(()=>messageLinks(props.text))
</script>
<style scoped>
a{color:#2563eb;text-decoration:underline;text-underline-offset:3px;overflow-wrap:anywhere}a:hover{color:#1d4ed8}
</style>
