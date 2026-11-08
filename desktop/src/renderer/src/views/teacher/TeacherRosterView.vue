<script setup lang="ts">
/**
 * TeacherRosterView —— 名册与班级（2026-09-26）。
 *
 * 这个面板回答一个问题：**这个班有谁？** 以及一件低频但必要的事：把
 * 账号开出来、把班级建起来。
 *
 * 为什么学生和班级现在挨着
 * ------------------------
 * 拆分前"学生清单"在学情看板的第五块、"班级管理"是一个单独的侧边栏项。
 * 但老师脑子里的模型只有一个——"我这个班"。名册和班级分开摆，等于让他
 * 在两个地方各找一半。
 *
 * **名册默认全部，班级是过滤器。** 这是本次重构里唯一一个需要拍板的取舍
 * （见对话记录）：现在的学生清单是全量的，而班级是分组的，两者不是同一
 * 个集合——一个还没加入任何班级的学生（或一次"所有人"作业的参与者）在
 * 班级视图里根本不存在。默认全部，选一个班才是缩小范围。
 *
 * 内部三段
 * --------
 *   名册  全部学生 + 按班过滤 + 开账号（单个 / 批量导入花名册）
 *   班级  新建、邀请码、成员、移出、删除
 *
 * 为什么不拆成两个侧边栏项：它们都会修改同一批数据，而任何一次修改之后
 * 两边都要刷新。分在两个页面里，"移出班级"之后名册上的班级标签就过期了，
 * 而老师不会知道要回去刷一下。
 *
 * 学生不能自己注册——花名册由任课老师建立，这也是这套系统里唯一会写入
 * 学生记录的操作。
 */

import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useDialog } from 'naive-ui'
import {
  NAlert,
  NButton,
  NCheckbox,
  NDropdown,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect
} from 'naive-ui'
import type { DropdownOption } from 'naive-ui'
import TeacherGate from '../../components/TeacherGate.vue'
import PageHeader from '../../components/PageHeader.vue'
import {
  createClassroom,
  deleteClassroom,
  listClassMembers,
  listClassrooms,
  removeClassMember,
  type ClassMemberOut,
  type ClassroomOut
} from '../../api/classroom'
import {
  createStudent,
  getStudents,
  importStudents,
  type ImportedStudent,
  type ImportStudentsResult,
  type StudentOut
} from '../../api/teacher'
import { useTeacherPanel } from './useTeacherPanel'
import { messageOf, percent, statusOf } from './format'

const loading = ref(false)
const errorMsg = ref('')

/**
 * 确认对话框。需要 `App.vue` 里有 `NDialogProvider`——没有 provider 时
 * `useDialog()` 返回的对象调用任何方法都会抛错，所以这个依赖是硬性的。
 */
const dialog = useDialog()

/**
 * 班级卡片「更多」里的操作。
 *
 * 只有删除。邀请码、查看名单、只看这个班都留在卡面上——它们是老师
 * 建完班之后真正每天做的事。删除是低频且不可逆的，收进菜单。
 */
const CLASS_MENU: DropdownOption[] = [{ label: '删除班级', key: 'delete' }]

function onClassMenu(room: ClassroomOut, key: string): void {
  if (key === 'delete') void dropClass(room)
}

/** 名册，全部学生。服务端按最差在前排序。 */
const students = ref<StudentOut[]>([])

/** 只看某个班，`null` 是全部。 */
const filterClassId = ref<number | null>(null)

const classrooms = ref<ClassroomOut[]>([])

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    const [stud, rooms] = await Promise.all([getStudents(), listClassrooms()])
    students.value = stud
    classrooms.value = rooms
    // 一个消失的班级（在这里或别处被删）不能让它的过滤条件留在屏幕上
    // ——那会变成一个"这个班里没有人"的假象。
    if (filterClassId.value !== null && !rooms.some((c) => c.id === filterClassId.value)) {
      filterClassId.value = null
    }
    if (openId.value !== null && !rooms.some((c) => c.id === openId.value)) {
      openId.value = null
      members.value = []
    }
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载名册')
  } finally {
    loading.value = false
  }
}

function reset(): void {
  students.value = []
  classrooms.value = []
  members.value = []
  openId.value = null
  filterClassId.value = null
}

useTeacherPanel(load, reset)

// --- 名册过滤 ---

/**
 * 当前过滤下要显示的学生。
 *
 * 按班过滤需要班级成员名单，而那份名单是按需拉的（一个班一次请求）。
 * 拉过就缓存，避免每切换一次过滤都等一个往返。
 */
const memberIdsByClass = ref<Record<number, number[]>>({})

const visibleStudents = computed<StudentOut[]>(() => {
  if (filterClassId.value === null) return students.value
  const ids = memberIdsByClass.value[filterClassId.value]
  if (!ids) return students.value
  const wanted = new Set(ids)
  return students.value.filter((s) => wanted.has(s.user_id))
})

async function pickClassFilter(id: number | null): Promise<void> {
  filterClassId.value = id
  if (id === null || memberIdsByClass.value[id]) return
  try {
    const rows = await listClassMembers(id)
    memberIdsByClass.value = {
      ...memberIdsByClass.value,
      [id]: rows.map((m) => m.user_id)
    }
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载班级成员')
  }
}

/**
 * 班级筛选下拉的选项（2026-09-26 从一排按钮改过来）。
 *
 * 第一项「全部学生」带总数——这个数原来写在按钮上，换成下拉后如果不带，
 * 它就消失了，而"我到底有多少学生"是这页最常看的一个数。
 * 哨兵用 `'__all__'` 而不是 `null`，理由同 `TeacherWorkView`：
 * NSelect 里 `null` 和"没选"分不开。
 */
const classFilterOptions = computed(() => [
  { label: `全部学生（${students.value.length}）`, value: '__all__' },
  ...classrooms.value.map((room) => ({
    label: `${room.name}（${room.member_count}）`,
    value: String(room.id)
  }))
])

/** 当前筛中的班名，用于工具栏下方那句"现在在看什么"。 */
const currentFilterName = computed(
  () => classrooms.value.find((r) => r.id === filterClassId.value)?.name ?? ''
)

// --- 班级 ---

const newClassName = ref('')
const creating = ref(false)
const createdHint = ref('')

/** 打开着成员名单的那个班，以及它的行。 */
const openId = ref<number | null>(null)
const members = ref<ClassMemberOut[]>([])
const membersLoading = ref(false)
const busyUserId = ref<number | null>(null)
const classError = ref('')

async function submitCreate(): Promise<void> {
  const name = newClassName.value.trim()
  if (!name || creating.value) return
  creating.value = true
  classError.value = ''
  createdHint.value = ''
  try {
    const room = await createClassroom(name)
    newClassName.value = ''
    createdHint.value = `已创建「${room.name}」，邀请码 ${room.invite_code}`
    await load()
  } catch (err) {
    classError.value = messageOf(err, '无法创建班级')
  } finally {
    creating.value = false
  }
}

async function toggleMembers(room: ClassroomOut): Promise<void> {
  if (openId.value === room.id) {
    openId.value = null
    members.value = []
    return
  }
  openId.value = room.id
  membersLoading.value = true
  members.value = []
  try {
    members.value = await listClassMembers(room.id)
    // 顺手把它喂给过滤器缓存：这一份名单干两件事。
    memberIdsByClass.value = {
      ...memberIdsByClass.value,
      [room.id]: members.value.map((m) => m.user_id)
    }
  } catch (err) {
    classError.value = messageOf(err, '无法加载班级成员')
  } finally {
    membersLoading.value = false
  }
}

async function dropMember(room: ClassroomOut, member: ClassMemberOut): Promise<void> {
  if (busyUserId.value !== null) return
  busyUserId.value = member.user_id
  classError.value = ''
  try {
    await removeClassMember(room.id, member.user_id)
    // 成员变了既影响这份名单也影响过滤器缓存，而缓存是**按 id 存**的，
    // 只重拉名单会让过滤结果继续显示一个已经被移出的人。
    const rows = await listClassMembers(room.id)
    members.value = rows
    const { [room.id]: _dropped, ...rest } = memberIdsByClass.value
    memberIdsByClass.value = { ...rest, [room.id]: rows.map((m) => m.user_id) }
    // 班级人数也要跟着变，所以整页重拉一次。
    await load()
  } catch (err) {
    classError.value = messageOf(err, `无法移除「${member.display_name}」`)
  } finally {
    busyUserId.value = null
  }
}

async function dropClass(room: ClassroomOut): Promise<void> {
  // 要确认，和复制作业相反：这会删掉一个老师可能花时间搭起来的班，
  // 而且它的作业会退回"所有人"可见——这是一次值一次额外点击的改动。
  //
  // 2026-09-26：`window.confirm` 换成 `dialog.warning`。原生确认框会**阻塞
  // 整个渲染进程**（Electron 里连窗口重绘都停），样式也不受主题控制；
  // 而且它在 `await` 里弹出来时，测试环境拿不到可控的返回值。
  const ok = await new Promise<boolean>((resolve) => {
    dialog.warning({
      title: '删除班级',
      content: `删除「${room.name}」？班级里的作业不会删除，会变成「所有人」可见。`,
      positiveText: '删除',
      negativeText: '取消',
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onMaskClick: () => resolve(false),
      onClose: () => resolve(false)
    })
  })
  if (!ok) return
  classError.value = ''
  try {
    await deleteClassroom(room.id)
    const { [room.id]: _dropped, ...rest } = memberIdsByClass.value
    memberIdsByClass.value = rest
    await load()
  } catch (err) {
    classError.value = messageOf(err, '无法删除班级')
  }
}

// --- 开账号 ---

const addingStudent = ref(false)
const newName = ref('')
const newDisplayName = ref('')
const newPassword = ref('')
const addError = ref('')
const addNotice = ref('')

async function submitNewStudent(): Promise<void> {
  if (!newName.value.trim() || !newPassword.value) {
    addError.value = '用户名和密码都不能为空'
    return
  }
  addError.value = ''
  addNotice.value = ''
  try {
    const created = await createStudent({
      username: newName.value.trim(),
      password: newPassword.value,
      display_name: newDisplayName.value.trim()
    })
    addNotice.value = `已创建账号 ${created.username}，初始密码已生效`
    newName.value = ''
    newDisplayName.value = ''
    newPassword.value = ''
    addingStudent.value = false
    await load()
  } catch (err) {
    // 409 值得单独一句话：这是这里唯一一个老师改个用户名就能解决的失败，
    // 混进通用文案会让他去找网络问题。
    addError.value =
      statusOf(err) === 409 ? '该用户名已被占用，换一个试试' : messageOf(err, '创建失败')
  }
}

// --- 批量导入 ---

const importingStudents = ref(false)
const importCsv = ref('')
const importDefaultPassword = ref('123456')
const importHasHeader = ref(true)
const importError = ref('')
const importResult = ref<ImportStudentsResult | null>(null)

/** 上次导入里失败的每一行，好让表单给出"具体要改什么"而不是一句汇总。 */
const importFailures = computed<ImportedStudent[]>(
  () => importResult.value?.results.filter((r) => !r.ok) ?? []
)

const importSample = `username,display_name
2025021120058,刘爱重
2025021120059,张三`

function fillImportSample(): void {
  importCsv.value = importSample
  importResult.value = null
  importError.value = ''
}

async function submitImportStudents(): Promise<void> {
  if (!importCsv.value.trim()) {
    importError.value = '请先粘贴名单'
    return
  }
  importError.value = ''
  importResult.value = null
  try {
    const result = await importStudents({
      csv: importCsv.value,
      default_password: importDefaultPassword.value || undefined,
      has_header: importHasHeader.value
    })
    importResult.value = result
    // 只有成功落库才清空输入框。带着一行错误重导一份名单是常态，
    // 把文本清掉会逼老师再粘一次才能改那一行。
    if (result.created > 0) importCsv.value = ''
    await load()
  } catch (err) {
    importError.value = messageOf(err, '导入失败')
  }
}

function closeImport(): void {
  importingStudents.value = false
  importCsv.value = ''
  importResult.value = null
  importError.value = ''
}

/** 学生所属的班级名，用来在名册上标注"他还没进任何班"。 */
function classNamesOf(userId: number): string[] {
  const out: string[] = []
  for (const room of classrooms.value) {
    if (memberIdsByClass.value[room.id]?.includes(userId)) out.push(room.name)
  }
  return out
}
</script>

<template>
  <div class="af-t-page">
    <div class="af-t-content">
      <PageHeader
        title="名册与班级"
        :subtitle="
          students.length > 0 ? `${students.length} 名学生 · ${classrooms.length} 个班级` : ''
        "
        :loading="loading"
        :error="errorMsg"
        error-prefix="无法加载名册"
        @refresh="load"
      />

      <TeacherGate>
        <!-- --- 名册 --- -->
        <section class="af-t-section">
          <h2 class="af-t-section-title">学生名册</h2>
          <p class="af-t-section-note">
            学生不能自己注册——花名册由任课老师建立，这也是这套系统里唯一会写入学生记录的操作。
          </p>

          <!-- 一条工具栏，两层信息分清楚（2026-09-26 重排）：
               左边是**看**（筛选 + 计数），右边是**写**（两个写入动作）。
               原来筛选是一排按钮、操作是另一排按钮，两排长得一样、
               又在同一屏里上下挨着——"看"和"写"混在一起了。
               而且筛选按钮会随班级数增长折行，五个班就占两行。

               换成下拉：十个班也只占一行，而且和「布置作业」里的
               「布置给」是同一个控件、同一套心智。 -->
          <div class="roster-toolbar">
            <div class="roster-toolbar-left">
              <NSelect
                v-if="classrooms.length"
                :value="filterClassId === null ? '__all__' : String(filterClassId)"
                :options="classFilterOptions"
                class="class-filter-select"
                @update:value="(v) => pickClassFilter(v === '__all__' ? null : Number(v))"
              />
            </div>
            <div class="roster-toolbar-right">
              <NButton type="primary" @click="addingStudent = true">添加学生</NButton>
              <NButton quaternary @click="importingStudents = true">批量导入名单</NButton>
            </div>
          </div>

          <p v-if="filterClassId !== null" class="af-t-section-note roster-filter-note">
            只看「{{ currentFilterName }}」的 {{ visibleStudents.length }} 名学生。
            <NButton text type="primary" size="tiny" @click="pickClassFilter(null)">
              显示全部
            </NButton>
          </p>

          <div v-if="students.length === 0 && !loading" class="af-t-empty">
            还没有学生账号。用上面的按钮添加，或批量导入一份名单。
          </div>

          <div v-else-if="visibleStudents.length === 0" class="af-t-empty">
            这个班里还没有学生。把下面的邀请码发给他们。
          </div>

          <div v-else class="af-t-table-scroll">
            <table class="af-t-table">
              <thead>
                <tr>
                  <th>学生</th>
                  <th>用户名</th>
                  <th class="af-t-num">提交</th>
                  <th class="af-t-num">正确率</th>
                  <th class="af-t-num">错题</th>
                  <th>班级</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in visibleStudents" :key="s.user_id">
                  <td class="af-t-strong-cell">{{ s.display_name || s.username }}</td>
                  <td class="muted-cell">{{ s.username }}</td>
                  <td class="af-t-num">{{ s.submissions }}</td>
                  <td class="af-t-num">{{ percent(s.accuracy) }}</td>
                  <td class="af-t-num">{{ s.mistakes }}</td>
                  <td>
                    <span v-if="classNamesOf(s.user_id).length" class="muted-cell">
                      {{ classNamesOf(s.user_id).join('、') }}
                    </span>
                    <span v-else class="muted-cell">未加入</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p class="af-t-section-note roster-crosslink">
            要看某个学生的成绩与错题？去
            <RouterLink class="af-t-link-btn" :to="{ name: 'teacher-analysis' }"
              >学情分析</RouterLink
            >
            （错题时间线）或
            <RouterLink class="af-t-link-btn" :to="{ name: 'teacher-work' }">作业</RouterLink>
            （成绩单）。
          </p>
        </section>

        <!-- --- 班级 --- -->
        <section class="af-t-section">
          <h2 class="af-t-section-title">班级</h2>
          <p class="af-t-section-note">
            把邀请码发给学生，他们用学生账号加入后就能收到你布置的作业。
          </p>

          <NAlert v-if="classError" type="error" :bordered="false" class="class-error">
            {{ classError }}
          </NAlert>

          <form class="class-create" @submit.prevent="submitCreate">
            <NInput
              v-model:value="newClassName"
              class="class-name-input"
              placeholder="例如 25 软件工程技术 2 班"
              maxlength="128"
              :disabled="creating"
            />
            <NButton type="primary" attr-type="submit" :loading="creating">新建班级</NButton>
          </form>
          <NAlert v-if="createdHint" type="success" :bordered="false" class="class-created">
            {{ createdHint }}
          </NAlert>

          <p v-if="classrooms.length === 0" class="af-t-empty class-empty">
            还没有班级。创建后把邀请码发给学生即可。
          </p>

          <ul v-else class="class-list">
            <li v-for="room in classrooms" :key="room.id" class="class-item">
              <!-- 卡片分三层（2026-09-26 重排）：
                   1. 标题行   班名 + 人数 —— 一眼认出是哪个班
                   2. 邀请码   全页最大的字，卡片的视觉中心
                   3. 操作行   查看名单 / 只看这个班 / 更多

                   原来「删除班级」和班名同在第一行、且是红色小字，
                   和「几个学生」并列着，看起来像同一类信息。删除是
                   毁数据的动作，不该和统计值挤在一行里。 -->
              <div class="class-head">
                <h3 class="class-name">{{ room.name }}</h3>
                <span class="class-count">{{ room.member_count }} 名学生</span>
                <NDropdown
                  trigger="click"
                  :options="CLASS_MENU"
                  @select="(key) => onClassMenu(room, String(key))"
                >
                  <NButton size="tiny" quaternary class="class-more">⋯</NButton>
                </NDropdown>
              </div>

              <!-- 页面上唯一会被**念出来**的值：对着一个班、用投影、
                   念一次。其余都是扫一眼的东西，所以它拿到页面上最大的
                   字号，并且字距拉开，好让三十个人一个字符一个字符地抄。 -->
              <div class="invite">
                <span class="invite-label">邀请码</span>
                <span class="invite-code">{{ room.invite_code }}</span>
              </div>

              <div class="class-actions">
                <NButton size="small" @click="toggleMembers(room)">
                  {{ openId === room.id ? '收起名单' : '查看名单' }}
                </NButton>
                <NButton
                  size="small"
                  quaternary
                  :class="{ 'is-current-filter': filterClassId === room.id }"
                  @click="pickClassFilter(room.id)"
                >
                  {{ filterClassId === room.id ? '正在只看这个班' : '只看这个班的学生' }}
                </NButton>
              </div>

              <div v-if="openId === room.id" class="class-members">
                <p v-if="membersLoading" class="af-t-placeholder-inline">正在加载…</p>
                <p v-else-if="members.length === 0" class="af-t-placeholder-inline">
                  还没有学生加入。把上面的邀请码发给他们。
                </p>
                <ul v-else class="member-list">
                  <li v-for="member in members" :key="member.user_id" class="member-item">
                    <span class="member-name">{{ member.display_name || member.username }}</span>
                    <span class="member-user">{{ member.username }}</span>
                    <NButton
                      size="tiny"
                      quaternary
                      :loading="busyUserId === member.user_id"
                      @click="dropMember(room, member)"
                    >
                      移出
                    </NButton>
                  </li>
                </ul>
              </div>
            </li>
          </ul>
        </section>
      </TeacherGate>
    </div>

    <!-- 添加学生（2026-09-26 由内联表单改为模态框）。
         这页有两个写入动作（建账号 / 批量导入），内联展开时它们会和名册表
         抢版面，而且两个表单可以同时展开、互相顶开。模态框一次只面对一件事。 -->
    <NModal
      v-model:show="addingStudent"
      preset="card"
      title="添加学生"
      :style="{ width: '460px', maxWidth: '92vw' }"
    >
      <NForm label-placement="top" @submit.prevent="submitNewStudent">
        <NFormItem label="用户名">
          <NInput v-model:value="newName" placeholder="登录用" />
        </NFormItem>
        <NFormItem label="姓名">
          <NInput v-model:value="newDisplayName" placeholder="显示用，可留空" />
        </NFormItem>
        <NFormItem label="初始密码">
          <NInput v-model:value="newPassword" placeholder="告诉学生本人" />
        </NFormItem>
        <NAlert v-if="addError" type="error" :bordered="false">{{ addError }}</NAlert>
      </NForm>

      <template #footer>
        <div class="af-t-modal-actions">
          <NButton @click="addingStudent = false">取消</NButton>
          <NButton type="primary" @click="submitNewStudent">创建</NButton>
        </div>
      </template>
    </NModal>

    <!-- 批量导入。逐行给结果：一个班的花名册里有一行写错，
         不能连累其余四十行不落库。所以在同一个弹窗里展示结果，
         而不是关掉再弹一条汇总——那几行错必须留在屏幕上给老师改。 -->
    <NModal
      v-model:show="importingStudents"
      preset="card"
      title="批量导入名单"
      :style="{ width: '560px', maxWidth: '94vw' }"
    >
      <NForm label-placement="top">
        <NFormItem label="名单（每行一个学生，逗号分隔）">
          <NInput
            v-model:value="importCsv"
            type="textarea"
            :rows="7"
            :autosize="{ minRows: 7, maxRows: 14 }"
            placeholder="username,display_name&#10;2025021120058,刘爱重"
          />
        </NFormItem>
        <p class="af-t-field-note modal-note">
          两列是「用户名,姓名」；也可写三列「用户名,姓名,密码」覆盖下面的初始密码。
          <NButton text type="primary" size="tiny" @click="fillImportSample">填入示例</NButton>
        </p>

        <NFormItem label="初始密码（没写密码的行用这个）">
          <NInput v-model:value="importDefaultPassword" />
        </NFormItem>

        <NCheckbox v-model:checked="importHasHeader">第一行是表头，跳过</NCheckbox>
      </NForm>

      <NAlert v-if="importError" type="error" :bordered="false" class="modal-alert">
        {{ importError }}
      </NAlert>

      <div v-if="importResult" class="import-result">
        <NAlert type="success" :bordered="false">
          成功 {{ importResult.created }} 人，跳过 {{ importResult.skipped }} 人。
        </NAlert>
        <!-- 失败逐行列出而不是只报个数：老师要一行行改，
             所以他要改的那几行必须在屏幕上。 -->
        <ul v-if="importFailures.length" class="import-failures">
          <li v-for="(f, i) in importFailures" :key="i">
            {{ f.username || '（空用户名）' }} · {{ f.display_name }} — {{ f.error }}
          </li>
        </ul>
      </div>

      <template #footer>
        <div class="af-t-modal-actions">
          <NButton @click="closeImport">取消</NButton>
          <NButton type="primary" @click="submitImportStudents">导入</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<script lang="ts">
export default { name: 'TeacherRosterView' }
</script>

<style scoped>
/* --- 名册工具栏（2026-09-26 重排） ---
 *
 * 一条横线，左边"看"、右边"写"，中间用 `margin-left: auto` 顶开。
 * 窄屏时折成上下两行，仍然是左边看完是右边写。
 *
 * 原来的写法是两排独立的按钮（筛选一排、操作一排），都居中左对齐，
 * 高度和间距也一样——视觉上读不出"这两种按钮不是一类东西"。
 */
.roster-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 12px 0;
}

.roster-toolbar-left {
  flex: 0 1 260px;
  min-width: 180px;
}

.roster-toolbar-right {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-left: auto;
}

.class-filter-select {
  width: 100%;
}

/* 工具栏下方那句"现在在看哪个班"。它和上面的下拉是一件事的两半：
 * 下拉说明**怎么筛**，这句说明**筛出来是什么样**。 */
.roster-filter-note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: -4px;
}

.import-result {
  margin-top: 4px;
}

.import-failures {
  margin: 6px 0 0 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--af-text-secondary);
}

.muted-cell {
  color: var(--af-text-muted);
}

.roster-crosslink {
  margin-top: 10px;
}

/* --- 班级 --- */
.class-error {
  margin-bottom: 8px;
}

.class-create {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.class-name-input {
  flex: 1 1 240px;
  max-width: 320px;
}

.class-created {
  margin-bottom: 8px;
}

.class-empty {
  margin-top: 12px;
}

.class-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

/* 单列纵向堆叠改成响应式网格：一个班一张卡，卡片之间等宽等距。
 * 原来是竖着排的一长条，三个班就要滚很久，而且卡片里横跨整行的
 * 元素（邀请码）会把卡拉得很宽，信息密度很低。 */
.class-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.class-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.class-name {
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 600;
}

.class-count {
  flex: none;
  font-size: 12px;
  color: var(--af-text-secondary);
}

.class-more {
  flex: none;
  margin-left: auto;
  font-size: 14px;
  line-height: 1;
  letter-spacing: 0.1em;
}

.invite {
  display: flex;
  align-items: center;
  gap: 10px;
}

.invite-label {
  flex: none;
  font-size: 11px;
  color: var(--af-text-muted);
}

/* 卡片里最大的字号，且是唯一允许 select-all 的值。 */
.invite-code {
  padding: 3px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 18px;
  letter-spacing: 0.25em;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  user-select: all;
}

.class-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 正在筛着这个班时，那个按钮是**状态**不是操作——所以它换个样子，
 * 而不是等着被再点一次。 */
.is-current-filter {
  color: var(--af-accent);
}

.class-members {
  padding-top: 10px;
  border-top: 1px solid var(--af-border);
}

.member-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.member-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 6px;
  font-size: 12px;
  border-radius: var(--af-radius-sm);
}

.member-item:hover {
  background: var(--af-elevated);
}

.member-name {
  color: var(--af-text-primary);
}

.member-user {
  color: var(--af-text-muted);
}
</style>
