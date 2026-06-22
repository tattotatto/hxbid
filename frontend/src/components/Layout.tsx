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
} from '@ant-design/icons'
import type { MenuProps } from 'antd'
import Copyright from './Copyright'

const { Header, Sider, Content, Footer } = AntLayout

type MenuItem = Required<MenuProps>['items'][number]

const menuItems: MenuItem[] = [
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
            fontWeight: 'bold',
            fontSize: collapsed ? 16 : 20,
            color: themeToken.colorPrimary,
            borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          }}
        >
          {collapsed ? '宏曦' : '宏曦标书'}
        </div>
        <Menu
          mode="inline"
          selectedKeys={getSelectedKeys()}
          defaultOpenKeys={getOpenKeys()}
          items={menuItems}
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
