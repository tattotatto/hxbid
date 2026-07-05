import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Login from './pages/Login'
import Workbench from './pages/Workbench'
import ProjectList from './pages/project/ProjectList'
import ProjectCreate from './pages/project/ProjectCreate'
import ProjectWorkflow from './pages/project/ProjectWorkflow'
import Qualifications from './pages/resources/Qualifications'
import Personnel from './pages/resources/Personnel'
import HistoryBids from './pages/resources/HistoryBids'
import Settings from './pages/settings/Settings'
import UserManagement from './pages/admin/UserManagement'
import CompanyInfo from './pages/resources/CompanyInfo'
import Contracts from './pages/resources/Contracts'

function App() {
  const token = localStorage.getItem('token')
  if (!token && window.location.pathname !== '/login') {
    return <Navigate to="/login" />
  }

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Layout />}>
        <Route index element={<Workbench />} />
        <Route path="projects" element={<ProjectList />} />
        <Route path="projects/new" element={<ProjectCreate />} />
        <Route path="projects/:id" element={<ProjectWorkflow />} />
        <Route path="resources/qualifications" element={<Qualifications />} />
        <Route path="resources/personnel" element={<Personnel />} />
        <Route path="resources/history" element={<HistoryBids />} />
        <Route path="resources/company" element={<CompanyInfo />} />
        <Route path="resources/contracts" element={<Contracts />} />
        <Route path="settings" element={<Settings />} />
        <Route path="admin/users" element={<UserManagement />} />
      </Route>
    </Routes>
  )
}

export default App
