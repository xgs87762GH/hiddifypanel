<script setup lang="ts">
/**
 * Empty state for a list that has no items at all.
 * Distinct from ListNoMatch, which is for when filters hide everything
 * (it offers a "reset filters" action). Use this one when the data set is
 * genuinely empty, optionally with a call-to-action in the default slot.
 */
defineProps<{
  icon?: string
  title: string
  hint?: string
}>()
</script>

<template>
  <div class="lempty" role="status">
    <i :class="icon || 'pi pi-inbox'" />
    <strong>{{ title }}</strong>
    <span v-if="hint">{{ hint }}</span>
    <slot />
  </div>
</template>

<style scoped>
.lempty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.35rem;
  padding: 2rem 1rem;
  text-align: center;
  color: var(--p-text-muted-color);
}
.lempty > i {
  font-size: 1.8rem;
  color: var(--p-primary-color, #6366f1);
}
.lempty strong {
  color: var(--p-text-color);
}
.lempty .p-button {
  margin-top: 0.5rem;
}
</style>
