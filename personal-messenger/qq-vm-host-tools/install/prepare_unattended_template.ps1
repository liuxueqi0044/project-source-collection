[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$vendorTemplate = 'C:\Program Files\Oracle\VirtualBox\UnattendedTemplates\win_nt6_unattended.xml'
$installDirectory = Split-Path -Parent $PSCommandPath
$templatePath = Join-Path $installDirectory 'win11_home_zh_cn_no_bypass.xml'

New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null
[xml]$template = Get-Content -LiteralPath $vendorTemplate -Raw
$namespace = [System.Xml.XmlNamespaceManager]::new($template.NameTable)
$namespace.AddNamespace('u', 'urn:schemas-microsoft-com:unattend')

$setup = $template.SelectSingleNode("/u:unattend/u:settings[@pass='windowsPE']/u:component[@name='Microsoft-Windows-Setup']", $namespace)
$setupBypass = $setup.SelectSingleNode('u:RunAsynchronous', $namespace)
if ($null -eq $setupBypass) { throw 'Expected Windows PE LabConfig block was not found.' }
[void]$setup.RemoveChild($setupBypass)

$disk = $setup.SelectSingleNode('u:DiskConfiguration/u:Disk', $namespace)
$createPartitions = $disk.SelectNodes('u:CreatePartitions', $namespace)
if ($createPartitions.Count -ne 2) { throw 'Expected BIOS and UEFI partition branches.' }
$createPartitions[1].InnerXml = '<CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>300</Size></CreatePartition><CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition><CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Size>80000</Size></CreatePartition><CreatePartition wcm:action="add"><Order>4</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>'
$modifyPartitions = $disk.SelectSingleNode('u:ModifyPartitions', $namespace)
if ($null -eq $modifyPartitions) { throw 'Expected UEFI ModifyPartitions branch.' }
$modifyPartitions.InnerXml = '<ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Label>System</Label><Format>FAT32</Format></ModifyPartition><ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>2</PartitionID></ModifyPartition><ModifyPartition wcm:action="add"><Order>3</Order><PartitionID>3</PartitionID><Label>Windows</Label><Letter>C</Letter><Format>NTFS</Format></ModifyPartition><ModifyPartition wcm:action="add"><Order>4</Order><PartitionID>4</PartitionID><Label>WINRE</Label><Format>NTFS</Format><TypeID>de94bba4-06d1-4d40-a16a-bfd50179d6ac</TypeID></ModifyPartition>'
$installPartitions = $setup.SelectNodes('u:ImageInstall/u:OSImage/u:InstallTo/u:PartitionID', $namespace)
if ($installPartitions.Count -ne 2) { throw 'Expected BIOS and UEFI InstallTo branches.' }
$installPartitions[1].InnerText = '3'

$deployment = $template.SelectSingleNode("/u:unattend/u:settings[@pass='specialize']/u:component[@name='Microsoft-Windows-Deployment']", $namespace)
$deploymentBypass = $deployment.SelectSingleNode('u:RunAsynchronous', $namespace)
if ($null -eq $deploymentBypass) { throw 'Expected specialize LabConfig block was not found.' }
[void]$deployment.RemoveChild($deploymentBypass)

$autoLogon = $template.SelectSingleNode("//u:settings[@pass='oobeSystem']/u:component[@name='Microsoft-Windows-Shell-Setup']/u:AutoLogon", $namespace)
if ($null -eq $autoLogon) { throw 'Expected AutoLogon block was not found.' }
$userName = $autoLogon.SelectSingleNode('u:Username', $namespace)
$logonCount = $template.CreateElement('LogonCount', 'urn:schemas-microsoft-com:unattend')
$logonCount.InnerText = '1'
[void]$autoLogon.InsertBefore($logonCount, $userName)

$settings = [System.Xml.XmlWriterSettings]::new()
$settings.Encoding = [System.Text.UTF8Encoding]::new($false)
$settings.Indent = $true
$writer = [System.Xml.XmlWriter]::Create($templatePath, $settings)
try { $template.Save($writer) } finally { $writer.Dispose() }

[pscustomobject]@{
    template = $templatePath
    removed_windows_pe_labconfig = $true
    removed_specialize_labconfig = $true
    accept_eula = $template.SelectSingleNode("//u:settings[@pass='windowsPE']/u:component[@name='Microsoft-Windows-Setup']/u:UserData/u:AcceptEula", $namespace).InnerText
    automatic_logon_count = $logonCount.InnerText
} | ConvertTo-Json
