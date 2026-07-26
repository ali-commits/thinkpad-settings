%global appid com.rabeei.ThinkPadSettings

Name:           thinkpad-settings
Version:        0.1.0
Release:        1%{?dist}
Summary:        Edit ThinkPad firmware settings from the desktop

License:        MIT
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

# PyGObject plus the two typelibs the app loads at import time. Fedora ships no
# virtual typelib() provides for these, so they are named directly.
Requires:       python3-gobject
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.4
# The entire read and write path is fwupd's D-Bus BIOS settings API. Without
# the daemon the application has nothing to talk to.
Requires:       fwupd >= 1.8
Requires:       hicolor-icon-theme

%description
A desktop editor for the firmware settings a Lenovo ThinkPad exposes to Linux
through the think_lmi kernel driver.

Reads and writes go through fwupd's D-Bus API, so the application runs
unprivileged and authenticates through polkit. Because fwupd accepts several
settings in one call, every staged change is written together and you are asked
to authenticate only once.

Settings are grouped into readable categories and each carries a plain-English
description of what it actually does on Linux. Changes that can lock you out of
your own machine are called out before they are written.

Requires a Lenovo ThinkPad whose firmware exposes settings through think_lmi.
Most settings take effect only after a restart.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files thinkpad_settings

install -Dpm0644 data/%{appid}.desktop \
    %{buildroot}%{_datadir}/applications/%{appid}.desktop
install -Dpm0644 data/%{appid}.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg
install -Dpm0644 data/%{appid}.metainfo.xml \
    %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml
install -Dpm0644 data/%{name}.1 %{buildroot}%{_mandir}/man1/%{name}.1

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{appid}.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml
# The GUI test suite needs a display and is not run during rpmbuild.
%pyproject_check_import

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/thinkpad-settings
%{_datadir}/applications/%{appid}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg
%{_metainfodir}/%{appid}.metainfo.xml
%{_mandir}/man1/%{name}.1*

%changelog
* Sun Jul 26 2026 Ali <ali@rabeei.com> - 0.1.0-1
- First release
