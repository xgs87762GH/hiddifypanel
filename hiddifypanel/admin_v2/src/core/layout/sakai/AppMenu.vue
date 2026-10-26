<script setup lang="ts">
import AppMenuItem from './AppMenuItem.vue'
import { useAdminMenu } from './composables/useAdminMenu'

const { menuGroups } = useAdminMenu()
</script>

<template>
  <ul class="layout-menu">
    <!-- AppMenuItem reads the node generically (path/url/action/badge), so it takes the loose shape. -->
    <AppMenuItem
      v-for="item in menuGroups"
      :key="item.label"
      :item="item as unknown as Record<string, unknown>"
      :root="true"
    />
  </ul>
</template>
