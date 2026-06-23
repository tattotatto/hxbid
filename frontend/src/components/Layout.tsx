import { useState } from 'react'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { Layout as AntLayout, Menu, Button, theme } from 'antd'
import {
  HomeOutlined,
  FileTextOutlined,
  DatabaseOutlined,
  SettingOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  HistoryOutlined,
  LogoutOutlined,
  UserOutlined,
} from '@ant-design/icons'
import type { MenuProps } from 'antd'
import Copyright from './Copyright'

const { Header, Sider, Content, Footer } = AntLayout

type MenuItem = Required<MenuProps>['items'][number]

function getUserRole(): string {
  try {
    const raw = localStorage.getItem('user')
    if (!raw) return 'viewer'
    return JSON.parse(raw).role || 'viewer'
  } catch {
    return 'viewer'
  }
}

function buildMenuItems(): MenuItem[] {
  const role = getUserRole()
  const items: MenuItem[] = [
    {
      key: '/',
      icon: <HomeOutlined />,
      label: '工作台',
    },
    {
      key: '/projects',
      icon: <FileTextOutlined />,
      label: '标书项目',
    },
    {
      key: 'resources',
      icon: <DatabaseOutlined />,
      label: '资源库',
      children: [
        {
          key: '/resources/qualifications',
          icon: <SafetyCertificateOutlined />,
          label: '公司资质',
        },
        {
          key: '/resources/personnel',
          icon: <TeamOutlined />,
          label: '人员信息',
        },
        {
          key: '/resources/history',
          icon: <HistoryOutlined />,
          label: '历史标书',
        },
      ],
    },
    {
      key: '/settings',
      icon: <SettingOutlined />,
      label: '系统设置',
    },
  ]

  // Admin-only menu items
  if (role === 'admin') {
    items.push({
      key: '/admin/users',
      icon: <UserOutlined />,
      label: '用户管理',
    })
  }

  return items
}

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { token: themeToken } = theme.useToken()

  const handleMenuClick: MenuProps['onClick'] = (e) => {
    navigate(e.key)
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    navigate('/login')
  }

  const getSelectedKeys = () => {
    const path = location.pathname
    if (path.startsWith('/resources/')) return [path]
    if (path === '/' || path.startsWith('/projects')) {
      if (path === '/') return ['/']
      return [path]
    }
    return [path]
  }

  const getOpenKeys = () => {
    const path = location.pathname
    if (path.startsWith('/resources/')) return ['resources']
    return []
  }

  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        style={{ background: themeToken.colorBgContainer }}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 10,
            padding: '0 12px',
            borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          }}
        >
          <img
            src="https://hongxikeji.oss-cn-chengdu.aliyuncs.com/%E5%AE%8F%E6%9B%A6%E7%A7%91%E6%8A%80logo-08.png"
            alt="logo"
            style={{ width: 36, height: 36, flexShrink: 0 }}
          />
          {!collapsed && (
            <span style={{ fontWeight: 'bold', fontSize: 18, color: themeToken.colorPrimary, whiteSpace: 'nowrap' }}>
              宏曦标书
            </span>
          )}
        </div>
        <Menu
          mode="inline"
          selectedKeys={getSelectedKeys()}
          defaultOpenKeys={getOpenKeys()}
          items={buildMenuItems()}
          onClick={handleMenuClick}
          style={{ borderInlineEnd: 'none' }}
        />
      </Sider>
      <AntLayout>
        <Header
          style={{
            padding: '0 24px',
            background: themeToken.colorBgContainer,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          }}
        >
          <Button
            type="text"
            icon={<LogoutOutlined />}
            onClick={handleLogout}
          >
            退出
          </Button>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
        <Footer style={{ textAlign: 'center' }}>
          <Copyright />
        </Footer>
      </AntLayout>
    </AntLayout>
  )
}
