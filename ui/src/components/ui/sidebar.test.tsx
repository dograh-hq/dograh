import { describe, it, expect } from 'vitest';
import { 
  Sidebar, 
  SidebarContent, 
  SidebarFooter, 
  SidebarGroup, 
  SidebarGroupContent, 
  SidebarGroupLabel, 
  SidebarHeader, 
  SidebarInput, 
  SidebarMenu, 
  SidebarMenuAction, 
  SidebarMenuButton, 
  SidebarMenuItem, 
  SidebarMenuSkeleton, 
  SidebarMenuSub, 
  SidebarMenuSubButton, 
  SidebarMenuSubItem, 
  SidebarProvider, 
  SidebarSeparator, 
  SidebarTrigger,
  useSidebar,
  SidebarInset
} from './sidebar';

describe('Sidebar', () => {
  it('exports all expected components', () => {
    expect(Sidebar).toBeDefined();
    expect(SidebarContent).toBeDefined();
    expect(SidebarFooter).toBeDefined();
    expect(SidebarGroup).toBeDefined();
    expect(SidebarGroupContent).toBeDefined();
    expect(SidebarGroupLabel).toBeDefined();
    expect(SidebarHeader).toBeDefined();
    expect(SidebarInput).toBeDefined();
    expect(SidebarMenu).toBeDefined();
    expect(SidebarMenuAction).toBeDefined();
    expect(SidebarMenuButton).toBeDefined();
    expect(SidebarMenuItem).toBeDefined();
    expect(SidebarMenuSkeleton).toBeDefined();
    expect(SidebarMenuSub).toBeDefined();
    expect(SidebarMenuSubButton).toBeDefined();
    expect(SidebarMenuSubItem).toBeDefined();
    expect(SidebarProvider).toBeDefined();
    expect(SidebarSeparator).toBeDefined();
    expect(SidebarTrigger).toBeDefined();
    expect(SidebarInset).toBeDefined();
  });

  it('exports useSidebar hook', () => {
    expect(useSidebar).toBeDefined();
    expect(typeof useSidebar).toBe('function');
  });

  it('SidebarProvider is a function component', () => {
    expect(typeof SidebarProvider).toBe('function');
  });

  it('Sidebar is a function component', () => {
    expect(typeof Sidebar).toBe('function');
  });

  it('SidebarContent is a function component', () => {
    expect(typeof SidebarContent).toBe('function');
  });

  it('SidebarHeader is a function component', () => {
    expect(typeof SidebarHeader).toBe('function');
  });

  it('SidebarFooter is a function component', () => {
    expect(typeof SidebarFooter).toBe('function');
  });

  it('SidebarGroup is a function component', () => {
    expect(typeof SidebarGroup).toBe('function');
  });

  it('SidebarGroupLabel is a function component', () => {
    expect(typeof SidebarGroupLabel).toBe('function');
  });

  it('SidebarGroupContent is a function component', () => {
    expect(typeof SidebarGroupContent).toBe('function');
  });

  it('SidebarMenu is a function component', () => {
    expect(typeof SidebarMenu).toBe('function');
  });

  it('SidebarMenuItem is a function component', () => {
    expect(typeof SidebarMenuItem).toBe('function');
  });

  it('SidebarMenuButton is a function component', () => {
    expect(typeof SidebarMenuButton).toBe('function');
  });

  it('SidebarMenuSkeleton is a function component', () => {
    expect(typeof SidebarMenuSkeleton).toBe('function');
  });

  it('SidebarSeparator is a function component', () => {
    expect(typeof SidebarSeparator).toBe('function');
  });

  it('SidebarTrigger is a function component', () => {
    expect(typeof SidebarTrigger).toBe('function');
  });

  it('SidebarInput is a function component', () => {
    expect(typeof SidebarInput).toBe('function');
  });

  it('SidebarInset is a function component', () => {
    expect(typeof SidebarInset).toBe('function');
  });

  it('SidebarMenuAction is a function component', () => {
    expect(typeof SidebarMenuAction).toBe('function');
  });

  it('SidebarMenuSub is a function component', () => {
    expect(typeof SidebarMenuSub).toBe('function');
  });

  it('SidebarMenuSubButton is a function component', () => {
    expect(typeof SidebarMenuSubButton).toBe('function');
  });

  it('SidebarMenuSubItem is a function component', () => {
    expect(typeof SidebarMenuSubItem).toBe('function');
  });
});