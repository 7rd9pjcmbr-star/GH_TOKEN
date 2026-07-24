# Atlas · post (435 modules)

**Học:** TTP sau xâm nhập → IOC persistence/lateral/cred
**Lab:** Viết IOC; không chạy post trên host ngoài lab
**Role:** ioc-triage

## Depth-1 (đầy đủ, không cắt)

- `post/windows` ×242
- `post/multi` ×79
- `post/linux` ×51
- `post/osx` ×23
- `post/hardware` ×12
- `post/android` ×7
- `post/networking` ×6
- `post/solaris` ×6
- `post/firefox` ×5
- `post/apple_ios` ×2
- `post/aix` ×1
- `post/bsd` ×1

## Depth-2 (đầy đủ, không cắt)

- `post/windows/gather` ×178
- `post/multi/gather` ×50
- `post/windows/manage` ×49
- `post/linux/gather` ×34
- `post/multi/manage` ×17
- `post/osx/gather` ×14
- `post/hardware/automotive` ×9
- `post/linux/busybox` ×8
- `post/linux/manage` ×7
- `post/networking/gather` ×6
- `post/windows/escalate` ×6
- `post/multi/recon` ×5
- `post/osx/manage` ×5
- `post/windows/wlan` ×5
- `post/firefox/gather` ×4
- `post/solaris/gather` ×4
- `post/android/gather` ×3
- `post/multi/escalate` ×3
- `post/multi/general` ×3
- `post/android/manage` ×2
- `post/apple_ios/gather` ×2
- `post/hardware/rftransceiver` ×2
- `post/osx/capture` ×2
- `post/solaris/escalate` ×2
- `post/windows/capture` ×2
- `post/windows/recon` ×2
- `post/aix/hashdump.rb` ×1
- `post/android/capture` ×1
- `post/android/local` ×1
- `post/bsd/gather` ×1
- `post/firefox/manage` ×1
- `post/hardware/zigbee` ×1
- `post/linux/capture` ×1
- `post/linux/dos` ×1
- `post/multi/sap` ×1
- `post/osx/admin` ×1
- `post/osx/escalate` ×1

## Depth-3 (337 nhánh — đủ key trong msf-atlas-depth3.json)

- `post/windows/gather/credentials` ×91
- `post/windows/gather/forensics` ×7
- `post/windows/manage/powershell` ×3
- `post/aix/hashdump.rb` ×1
- `post/android/capture/screen.rb` ×1
- `post/android/gather/hashdump.rb` ×1
- `post/android/gather/sub_info.rb` ×1
- `post/android/gather/wireless_ap.rb` ×1
- `post/android/local/koffee.rb` ×1
- `post/android/manage/remove_lock.rb` ×1
- `post/android/manage/remove_lock_root.rb` ×1
- `post/apple_ios/gather/ios_image_gather.rb` ×1
- `post/apple_ios/gather/ios_text_gather.rb` ×1
- `post/bsd/gather/hashdump.rb` ×1
- `post/firefox/gather/cookies.rb` ×1
- `post/firefox/gather/history.rb` ×1
- `post/firefox/gather/passwords.rb` ×1
- `post/firefox/gather/xss.rb` ×1
- `post/firefox/manage/webcam_chat.rb` ×1
- `post/hardware/automotive/can_flood.rb` ×1
- `post/hardware/automotive/canprobe.rb` ×1
- `post/hardware/automotive/diagnostic_state.rb` ×1
- `post/hardware/automotive/ecu_hard_reset.rb` ×1
- `post/hardware/automotive/getvinfo.rb` ×1
- `post/hardware/automotive/identifymodules.rb` ×1
- `post/hardware/automotive/malibu_overheat.rb` ×1
- `post/hardware/automotive/mazda_ic_mover.rb` ×1
- `post/hardware/automotive/pdt.rb` ×1
- `post/hardware/rftransceiver/rfpwnon.rb` ×1
- `post/hardware/rftransceiver/transmitter.rb` ×1
- `post/hardware/zigbee/zstumbler.rb` ×1
- `post/linux/busybox/enum_connections.rb` ×1
- `post/linux/busybox/enum_hosts.rb` ×1
- `post/linux/busybox/jailbreak.rb` ×1
- `post/linux/busybox/ping_net.rb` ×1
- `post/linux/busybox/set_dmz.rb` ×1
- `post/linux/busybox/set_dns.rb` ×1
- `post/linux/busybox/smb_share_root.rb` ×1
- `post/linux/busybox/wget_exec.rb` ×1
- `post/linux/capture/grandstream_gxp1600_sip.rb` ×1
- `post/linux/dos/xen_420_dos.rb` ×1
- `post/linux/gather/ansible.rb` ×1
- `post/linux/gather/ansible_playbook_error_message_file_reader.rb` ×1
- `post/linux/gather/apache_nifi_credentials.rb` ×1
- `post/linux/gather/checkcontainer.rb` ×1
- `post/linux/gather/checkvm.rb` ×1
- `post/linux/gather/cve_2026_46333_chage.rb` ×1
- `post/linux/gather/ecryptfs_creds.rb` ×1
- `post/linux/gather/enum_commands.rb` ×1
- `post/linux/gather/enum_configs.rb` ×1
- `post/linux/gather/enum_containers.rb` ×1
- `post/linux/gather/enum_nagios_xi.rb` ×1
- `post/linux/gather/enum_network.rb` ×1
- `post/linux/gather/enum_protections.rb` ×1
- `post/linux/gather/enum_psk.rb` ×1
- `post/linux/gather/enum_system.rb` ×1
- `post/linux/gather/enum_users_history.rb` ×1
- `post/linux/gather/f5_loot_mcp.rb` ×1
- `post/linux/gather/gnome_commander_creds.rb` ×1
- `post/linux/gather/gnome_keyring_dump.rb` ×1
- `post/linux/gather/grandstream_gxp1600_creds.rb` ×1
- `post/linux/gather/haserl_read.rb` ×1
- `post/linux/gather/hashdump.rb` ×1
- `post/linux/gather/igel_dump_file.rb` ×1
- `post/linux/gather/manageengine_password_manager_creds.rb` ×1
- `post/linux/gather/mimipenguin.rb` ×1
- `post/linux/gather/mount_cifs_creds.rb` ×1
- `post/linux/gather/openvpn_credentials.rb` ×1
- `post/linux/gather/phpmyadmin_credsteal.rb` ×1
- `post/linux/gather/pptpd_chap_secrets.rb` ×1
- `post/linux/gather/puppet.rb` ×1
- `post/linux/gather/rancher_audit_log_leak.rb` ×1
- `post/linux/gather/tenable_security_center.rb` ×1
- `post/linux/gather/tor_hiddenservices.rb` ×1
- `post/linux/gather/vcenter_secrets_dump.rb` ×1
- `post/linux/manage/adduser.rb` ×1
- `post/linux/manage/disable_clamav.rb` ×1
- `post/linux/manage/dns_spoofing.rb` ×1
- `post/linux/manage/download_exec.rb` ×1
- `post/linux/manage/geutebruck_post_exp.rb` ×1
- `post/linux/manage/iptables_removal.rb` ×1
- `post/linux/manage/pseudo_shell.rb` ×1
- `post/multi/escalate/aws_create_iam_user.rb` ×1
- `post/multi/escalate/cups_root_file_read.rb` ×1
- `post/multi/escalate/metasploit_pcaplog.rb` ×1
- `post/multi/gather/apple_ios_backup.rb` ×1
- `post/multi/gather/aws_ec2_instance_metadata.rb` ×1
- `post/multi/gather/aws_keys.rb` ×1
- `post/multi/gather/azure_cli_creds.rb` ×1
- `post/multi/gather/check_malware.rb` ×1
- `post/multi/gather/chrome_cookies.rb` ×1
- `post/multi/gather/dbeaver.rb` ×1
- `post/multi/gather/dbvis_enum.rb` ×1
- `post/multi/gather/dns_bruteforce.rb` ×1
- `post/multi/gather/dns_reverse_lookup.rb` ×1
- `post/multi/gather/dns_srv_lookup.rb` ×1
- `post/multi/gather/docker_creds.rb` ×1
- `post/multi/gather/electerm.rb` ×1
- `post/multi/gather/enum_hexchat.rb` ×1
- `post/multi/gather/enum_software_versions.rb` ×1
- … +237 nhánh nữa (xem JSON)

> Nguồn: `python3 scripts/metasploit_full_atlas.py`
> Cấm: exploit run · msfvenom · scan prod

